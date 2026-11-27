import asyncio
import tarfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from csdr.cli_dataset_gmw import run_extract_gmw


def _make_tif_bytes() -> bytes:
    data = np.ones((2, 2), dtype="uint8")
    transform = from_origin(0, 2, 1, 1)
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff",
            height=2,
            width=2,
            count=1,
            dtype="uint8",
            crs="EPSG:4326",
            transform=transform,
        ) as dataset:
            dataset.write(data, 1)
        return memfile.read()


def _write_zip(archive_path: Path, members: list[tuple[str, bytes]]) -> None:
    with ZipFile(archive_path, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)


def _write_tar_gz(archive_path: Path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(archive_path, "w:gz") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, BytesIO(data))


@pytest.mark.parametrize(
    "archive_name,write_archive",
    [
        ("sample.zip", _write_zip),
        ("sample.tar.gz", _write_tar_gz),
    ],
)
def test_run_extract_gmw_writes_cog_and_stac_item(
    tmp_path: Path,
    archive_name: str,
    write_archive: Callable[[Path, list[tuple[str, bytes]]], None],
) -> None:
    member_name = "sample_v4.tif"
    write_archive(tmp_path / archive_name, [(member_name, _make_tif_bytes())])

    target_location = tmp_path / "target"

    asyncio.run(
        run_extract_gmw(
            source_location=str(tmp_path),
            source_zip_name=archive_name,
            target_location=str(target_location),
            overwrite=True,
            max_concurrent=4,
        )
    )

    assert (target_location / member_name).exists()
    assert (target_location / "sample_v4.stac-item.json").exists()


@pytest.mark.parametrize(
    "archive_name,write_archive",
    [
        ("sample.zip", _write_zip),
        ("sample.tar.gz", _write_tar_gz),
    ],
)
def test_run_extract_gmw_skips_resource_forks_and_normalizes_dot_slash(
    tmp_path: Path,
    archive_name: str,
    write_archive: Callable[[Path, list[tuple[str, bytes]]], None],
) -> None:
    tif_bytes = _make_tif_bytes()
    write_archive(
        tmp_path / archive_name,
        [
            ("./dir/real_v4.tif", tif_bytes),
            # macOS AppleDouble resource-fork metadata, not a real GeoTIFF.
            ("./dir/._real_v4.tif", tif_bytes),
        ],
    )

    target_location = tmp_path / "target"

    asyncio.run(
        run_extract_gmw(
            source_location=str(tmp_path),
            source_zip_name=archive_name,
            target_location=str(target_location),
            overwrite=True,
            max_concurrent=4,
        )
    )

    assert (target_location / "dir" / "real_v4.tif").exists()
    assert (target_location / "dir" / "real_v4.stac-item.json").exists()
    assert not (target_location / "dir" / "._real_v4.tif").exists()
    assert not (target_location / "dir" / "._real_v4.stac-item.json").exists()
