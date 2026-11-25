"""Indicator registry: regex families plus an exact-id allow-list.

An indicator id (the key in ``--indicators-to-extract``, equal to the app's
measured indicator id) is accepted when it matches a family pattern or is in
``KNOWN_INDICATORS``. Each family names the handler that computes it in
``csdr.products.process_indicators_for_geometry``. A family whose handler is
``None`` is *declared*: ids validate, but processing refuses them with a clear
error until a build adds the handler. Families with ``depends_on`` run after
the family they depend on, in the same geometry run.

``csdr helpers indicator-families`` prints this registry. Keep this module free
of other ``csdr`` imports so any module can use it.
"""

import re
from dataclasses import dataclass, field

# Exact ids kept for backwards compatibility. Every id here also matches a family;
# new ids need no entry here when they follow a family.
KNOWN_INDICATORS = [
    "sum-mangrove-area",  # Used for GMW v3, GMW v4, and ACE
    "sum-seagrass-area",  # Used for seagrass and ACE
    "sum-reef-area",
    "count-buildings",
    # ACE indicators:
    "sum-intertidal-area",
    "sum-saltmarsh-area",
    "percent-mangrove-area",
    "percent-intertidal-area",
    "percent-saltmarsh-area",
    "percent-seagrass-area",
]


@dataclass(frozen=True)
class IndicatorFamily:
    """One indicator family: an id pattern and the handler that computes it."""

    name: str
    template: str
    pattern: re.Pattern = field(compare=False)
    handler: str | None
    description: str
    depends_on: str | None = None

    @property
    def implemented(self) -> bool:
        return self.handler is not None

    @property
    def status(self) -> str:
        return "implemented" if self.implemented else "declared"


def _family(
    name: str,
    template: str,
    regex: str,
    handler: str | None,
    description: str,
    depends_on: str | None = None,
) -> IndicatorFamily:
    return IndicatorFamily(
        name=name,
        template=template,
        pattern=re.compile(regex),
        handler=handler,
        description=description,
        depends_on=depends_on,
    )


# Handler names are dispatched in csdr.products.process_indicators_for_geometry.
HANDLER_AREA_SUM = "area-sum"
HANDLER_AREA_PERCENT = "area-percent"
HANDLER_FEATURE_COUNT = "feature-count"
# One pass per raster band computes every zonal statistic of that band.
HANDLER_ZONAL_STATISTICS = "zonal-statistics"

INDICATOR_FAMILIES: tuple[IndicatorFamily, ...] = (
    _family(
        "sum-area",
        "sum-<x>-area",
        r"^sum-.*-area$",
        HANDLER_AREA_SUM,
        "Area in m² of the selected raster classes within the geometry, or of "
        "the vector features that intersect it.",
    ),
    _family(
        "percent-area",
        "percent-<x>-area",
        r"^percent-.*-area$",
        HANDLER_AREA_PERCENT,
        "sum-<x>-area as a percentage of the geometry's area. Request "
        "sum-<x>-area in the same run.",
        depends_on="sum-area",
    ),
    _family(
        "count",
        "count-<x>",
        r"^count-.*$",
        HANDLER_FEATURE_COUNT,
        "Count of features intersecting the geometry (indexed partitioned "
        "GeoParquet such as building footprints).",
    ),
    _family(
        "mean",
        "mean-<x>",
        r"^mean-.+$",
        HANDLER_ZONAL_STATISTICS,
        "Mean of valid raster cells within the geometry (float64 sum / valid cells).",
    ),
    _family(
        "p10",
        "p10-<x>",
        r"^p10-.+$",
        HANDLER_ZONAL_STATISTICS,
        "10th percentile of valid raster cells within the geometry "
        "(--percentile-method linear or histogram).",
    ),
    _family(
        "p25",
        "p25-<x>",
        r"^p25-.+$",
        HANDLER_ZONAL_STATISTICS,
        "25th percentile of valid raster cells within the geometry "
        "(--percentile-method linear or histogram).",
    ),
    _family(
        "p50",
        "p50-<x>",
        r"^p50-.+$",
        HANDLER_ZONAL_STATISTICS,
        "Median of valid raster cells within the geometry "
        "(--percentile-method linear or histogram).",
    ),
    _family(
        "p75",
        "p75-<x>",
        r"^p75-.+$",
        HANDLER_ZONAL_STATISTICS,
        "75th percentile of valid raster cells within the geometry "
        "(--percentile-method linear or histogram).",
    ),
    _family(
        "p90",
        "p90-<x>",
        r"^p90-.+$",
        HANDLER_ZONAL_STATISTICS,
        "90th percentile of valid raster cells within the geometry "
        "(--percentile-method linear or histogram).",
    ),
    _family(
        "valid-fraction",
        "valid-fraction-<x>",
        r"^valid-fraction-.+$",
        HANDLER_ZONAL_STATISTICS,
        "Valid raster cells divided by raster cells inside the geometry and the "
        "source extent (nodata cells included).",
    ),
    _family(
        "distinct-count",
        "distinct-<x>-count",
        r"^distinct-.+-count$",
        HANDLER_ZONAL_STATISTICS,
        "Number of distinct class values among valid raster cells, optionally "
        "restricted by indicator-value.",
    ),
)

_BY_NAME = {family.name: family for family in INDICATOR_FAMILIES}


def family_for(indicator_id: str) -> IndicatorFamily | None:
    """The family an indicator id belongs to (first match), or None."""
    for family in INDICATOR_FAMILIES:
        if family.pattern.fullmatch(indicator_id):
            return family
    return None


def is_known_indicator(indicator_id: str) -> bool:
    return indicator_id in KNOWN_INDICATORS or family_for(indicator_id) is not None


def unknown_indicators(indicator_ids: list[str]) -> list[str]:
    return [i for i in indicator_ids if not is_known_indicator(i)]


def unknown_indicator_message(unknown: list[str]) -> str:
    families = ", ".join(
        f"{family.template} ({family.status})" for family in INDICATOR_FAMILIES
    )
    return (
        f"Unknown indicator to extract: {unknown}. An indicator id must match a "
        f"family: {families}; or be one of the exact ids: {KNOWN_INDICATORS}. "
        "Run 'csdr helpers indicator-families' to list the registry."
    )


def dependency_stage(family: IndicatorFamily | None) -> int:
    """0 for independent families, 1 + the stage of the family depended on."""
    stage = 0
    while family is not None and family.depends_on is not None:
        stage += 1
        family = _BY_NAME[family.depends_on]
    return stage


def order_indicators(indicators: dict[str, dict]) -> dict[str, dict]:
    """Order indicators so a dependent family runs after the family it needs."""
    return dict(
        sorted(
            indicators.items(),
            key=lambda item: (dependency_stage(family_for(item[0])), item[0]),
        )
    )


def registry_rows() -> list[dict[str, str | None]]:
    """The registry as plain rows (for the CLI table and --json)."""
    return [
        {
            "family": family.name,
            "idPattern": family.template,
            "regex": family.pattern.pattern,
            "status": family.status,
            "handler": family.handler,
            "dependsOn": (
                _BY_NAME[family.depends_on].template if family.depends_on else None
            ),
            "description": family.description,
        }
        for family in INDICATOR_FAMILIES
    ]
