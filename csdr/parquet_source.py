"""Bounded S3 access for Parquet readers without session-token support.

Three cases, decided before any bytes are read:

* A single S3 object with resolvable AWS credentials is staged to disk with the
  normal boto3 chain (profile, environment, pod role, temporary credentials),
  pinned by ETag and VersionId, after a size and disk-space check. The reader
  gets a local path. No credentials are handed to the reader.
* An S3 prefix (a key ending in ``/``, or a key with no object but with
  children) is passed through unchanged. Partitioned datasets such as
  ``.../partition/`` are read in place by the reader, anonymously.
* With no resolvable credentials, public objects are read anonymously: passed
  through to the reader, or staged with an unsigned client when the caller
  needs a local file. The region comes from AWS_REGION or AWS_DEFAULT_REGION.

Pair a yielded value with :func:`sedona_options` when reading with SedonaDB.
An authenticated read that fails never retries anonymously.

Only new engines (``csdr.vector_aggregate``) use this module; the existing
``geoparquet`` reads in ``csdr.products`` open their sources as before.
"""

import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import SplitResult, unquote, urlsplit

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from csdr.utils import CSDRException

# Region of anonymous reads when neither AWS_REGION nor AWS_DEFAULT_REGION is set,
# the same default as the toolkit's other unsigned reads.
DEFAULT_REGION = "ap-southeast-2"
_MISSING = ("404", "NoSuchKey", "NotFound")


def aws_region() -> str:
    """Region for anonymous reads: AWS_REGION, then AWS_DEFAULT_REGION."""
    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or DEFAULT_REGION
    )


def credentials_available() -> bool:
    """True when the boto3 chain resolves credentials; never raises."""
    try:
        return boto3.Session().get_credentials() is not None
    except (BotoCoreError, ClientError):
        return False


def sedona_options(readable: str) -> dict[str, object]:
    """SedonaDB options for a value yielded by :func:`parquet_source`.

    Local paths need none. An ``s3://`` value is only ever yielded for an
    anonymous read (a prefix, or no credentials), so it is read unsigned.
    """
    if urlsplit(readable).scheme.lower() != "s3":
        return {}
    return {"aws.skip_signature": True, "aws.region": aws_region()}


def _client(signed: bool) -> object:
    if signed:
        return boto3.client("s3")
    return boto3.client(
        "s3", region_name=aws_region(), config=Config(signature_version=UNSIGNED)
    )


def _validated(url: str) -> SplitResult:
    source = urlsplit(url)
    if (
        not source.netloc
        or source.username
        or source.port
        or source.query
        or source.fragment
        or not source.path.lstrip("/")
    ):
        raise CSDRException(
            "Provide an S3 object or prefix URL without query parameters."
        )
    return source


def _is_prefix(client: object, bucket: str, key: str) -> bool:
    """A missing object whose key names a folder with children is a prefix."""
    response = client.list_objects_v2(
        Bucket=bucket, Prefix=key.rstrip("/") + "/", MaxKeys=1
    )
    return bool(response.get("KeyCount") or response.get("Contents"))


def _unreadable(signed: bool) -> CSDRException:
    # SDK errors can include endpoint details. Do not expose credentials.
    if signed:
        return CSDRException(
            "Could not read the private Parquet source. Check AWS credentials, "
            "object permissions and whether the source version changed."
        )
    return CSDRException(
        "Could not read the Parquet source anonymously. No AWS credentials were "
        "found; provide credentials or check that the object is public."
    )


@contextmanager
def parquet_source(
    url: str, *, max_bytes: int | None = None, local: bool = False
) -> Iterator[str]:
    """Yield a path or URL a Parquet reader can open, kept alive for the block.

    ``local=True`` always yields a staged file (for hashing or in-memory
    readers) and refuses prefixes. Otherwise prefixes and anonymous reads pass
    through without staging. Staging uses disk, not memory, and is removed on
    success and failure. Callers must record the original URL in provenance,
    never a temporary path.
    """
    if urlsplit(url).scheme.lower() != "s3":
        yield url
        return
    source = _validated(url)
    bucket, key = source.netloc, unquote(source.path.lstrip("/"))
    if key.endswith("/"):
        if local:
            raise CSDRException(
                "This method needs a single S3 object. Provide the object URL, not a prefix."
            )
        yield url
        return
    signed = credentials_available()
    if not signed and not local:
        yield url
        return
    limit = (
        max_bytes
        if max_bytes is not None
        else int(os.environ.get("CSDR_PRIVATE_PARQUET_MAX_BYTES", str(8 * 1024**3)))
    )
    if limit <= 0:
        raise CSDRException("The private Parquet staging limit must be positive.")
    client = _client(signed)
    with tempfile.TemporaryDirectory(prefix="csdr-parquet-source-") as directory:
        target = Path(directory) / "source.parquet"
        prefix = False
        try:
            try:
                head = client.head_object(Bucket=bucket, Key=key)
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") not in _MISSING:
                    raise
                prefix = not local and _is_prefix(client, bucket, key)
                if not prefix:
                    raise
            if not prefix:
                _stage(client, bucket, key, head, target, limit, versioned=signed)
        except CSDRException:
            raise
        # Deliberate catch-all: SDK and transport errors can carry endpoint
        # details, so every failure becomes one sanitised message.
        except Exception:  # noqa: BLE001
            raise _unreadable(signed) from None
        if prefix:
            yield f"s3://{bucket}/{source.path.strip('/')}/"
        else:
            yield str(target)


def _stage(
    client: object,
    bucket: str,
    key: str,
    head: dict,
    target: Path,
    limit: int,
    *,
    versioned: bool,
) -> None:
    size = head["ContentLength"]
    if size > limit or size > shutil.disk_usage(target.parent).free:
        raise CSDRException(
            "The private source exceeds the staging limit or available disk. "
            "Partition the source or provision more worker scratch space."
        )
    identity = {"IfMatch": head["ETag"]}
    # Anonymous principals usually lack s3:GetObjectVersion; the ETag still pins bytes.
    if versioned and head.get("VersionId"):
        identity["VersionId"] = head["VersionId"]
    response = client.get_object(Bucket=bucket, Key=key, **identity)
    written = 0
    with closing(response["Body"]) as body, target.open("xb") as output:
        target.chmod(0o600)
        for chunk in body.iter_chunks(chunk_size=8 * 1024**2):
            written += len(chunk)
            if written > size or written > limit:
                raise CSDRException("The private source exceeded its declared size.")
            output.write(chunk)
    if written != size:
        raise CSDRException("The private source download was incomplete.")
