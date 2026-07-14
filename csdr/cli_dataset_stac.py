# Generic STAC-Geoparquet indexer for static STAC items (e.g. COG mosaics).
#
# Unlike `ace index` / `seagrass index`, which query a live STAC API with rustac's
# `search_to`, this command reads an explicit list of static STAC item JSON documents
# (local, http(s):// or s3://) and writes them to a single STAC-Geoparquet with
# rustac.write. This is the same primitive `gmw index` uses, and is the path that works
# for a static STAC catalog (which a STAC-API search cannot crawl).
#
# It is dataset-agnostic: any dataset delivered as static STAC items referencing COGs can
# be indexed with it. The optional --asset-name normalises a single-band COG's asset key so
# that a downstream `odc.stac.load` (as used by `products process-geometry`) names the
# loaded variable predictably, without each product needing a per-dataset `bands` override.
import asyncio
import logging

import typer
from rustac import write

from csdr.cli_helpers import parse_csv_list
from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
    read_dict,
    split_path_and_file_name_from_url,
)
from csdr.provenance import write_step
from csdr.utils import CSDRException, suppress_rust_output

stac_app = typer.Typer()
logger = logging.getLogger(__name__)


def _rename_asset(item: dict, asset_name: str) -> dict:
    """Rename a single-band item's one asset key to asset_name.

    `odc.stac.load` names the loaded xarray variable after the asset key, so normalising it
    here lets products select the band by a stable, dataset-specific name (e.g. 'global_seagrass').
    """
    assets = item.get("assets", {})
    if len(assets) != 1:
        raise CSDRException(
            f"--asset-name requires exactly one asset per item, but item "
            f"'{item.get('id')}' has {len(assets)}: {list(assets)}"
        )
    (old_key,) = tuple(assets.keys())
    if old_key != asset_name:
        assets[asset_name] = assets.pop(old_key)
        logger.info(f"Renamed asset '{old_key}' -> '{asset_name}' for item '{item.get('id')}'")
    return item


async def run_index_stac_items(
    source_items: list[str],
    target_location: str,
    target_filename: str,
    asset_name: str | None = None,
    overwrite: bool = True,
) -> int:
    """Read each STAC item JSON and write them to a single STAC-Geoparquet. Returns item count."""
    file_name = f"{target_filename}.parquet"
    target_store = get_store_with_prefix_from_url(target_location, mkdir=True)
    target_url = f"{target_location}/{file_name}"
    logger.info(f"Target URL: {target_url}")

    if exists(target_store, file_name) and not overwrite:
        logger.info(f"Parquet file already exists at {target_url}, skipping indexing.")
        return len(source_items)
    else:
        if overwrite:
            logger.info("Overwrite is enabled, re-indexing.")
        else:
            logger.info("Parquet file does not exist, proceeding with indexing.")

    item_dicts = []
    for source_url in source_items:
        source_path, source_file_name = split_path_and_file_name_from_url(source_url)
        source_store = get_store_with_prefix_from_url(source_path)
        if not exists(source_store, source_file_name):
            raise CSDRException(f"Source STAC item does not exist at {source_url}.")
        item = read_dict(source_store, source_file_name)
        if asset_name is not None:
            item = _rename_asset(item, asset_name)
        item_dicts.append(item)

    logger.info(f"Writing {len(item_dicts)} STAC items to parquet at {target_url}")
    with suppress_rust_output():
        # rustac infers the parquet format from the filename.
        # TODO: experiment with parquet_compression options for rustac write
        await write(file_name, item_dicts, store=target_store)

    logger.info(f"Finished writing parquet file to {target_url}")
    return len(item_dicts)


# Read an explicit list of static STAC item JSONs and index them into a single STAC-Geoparquet.
@stac_app.command("index")
def index_stac(
    source_stac_items: str = typer.Option(
        ...,
        help="Comma-separated list of STAC item JSON URLs (local, http(s):// or s3://).",
    ),
    target_location: str = typer.Option(
        ...,
        help="Local or remote path (local or s3://) to store the indexed parquet file.",
    ),
    target_filename: str = typer.Option(
        ...,
        help="Name of the target parquet file (without extension).",
    ),
    asset_name: str | None = typer.Option(
        None,
        help="Optional: rename each single-band item's one asset key to this value, so the loaded "
        "variable name is stable and dataset-specific (e.g. 'global_seagrass').",
    ),
    dataset_name: str = typer.Option(
        "STAC",
        help="Name of the dataset being indexed, used for provenance and logging.",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logger.info(f"Starting {dataset_name} STAC indexing process...")
    source_items = [s.strip() for s in parse_csv_list(source_stac_items) if s.strip()]
    if not source_items:
        raise CSDRException("No source STAC items provided to --source-stac-items.")
    item_count = asyncio.run(
        run_index_stac_items(
            source_items,
            target_location,
            target_filename,
            asset_name=asset_name,
            overwrite=overwrite,
        )
    )
    logger.info(f"{dataset_name} STAC indexing process completed.")
    write_step(
        label=f"Index {item_count} {dataset_name} STAC items into a single parquet file",
        inputs={"source_stac_items": source_items, "asset_name": asset_name},
        outputs={"target_file": f"{target_location}/{target_filename}.parquet"},
    )
