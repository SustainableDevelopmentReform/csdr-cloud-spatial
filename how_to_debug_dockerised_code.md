# Debugging Dockerised Code

Context: I had issues with code in Docker that were not present locally.

### Build the image:

`docker build -t csdr-local .`

When changing test_read.py, you then need to rebuild the docker image before running again.

### Run the image (for python):

```
docker run --rm -it \
  -e AWS_ACCESS_KEY_ID="" \
  -e AWS_SECRET_ACCESS_KEY="/" \
  -e AWS_SESSION_TOKEN="" \
  -e AWS_DEFAULT_REGION=ap-southeast-2 \
  csdr-local python test_read.py`
```

### Run the image (for make command):

```
docker run --rm -it \
  -e AWS_ACCESS_KEY_ID="" \
  -e AWS_SECRET_ACCESS_KEY="/" \
  -e AWS_SESSION_TOKEN="" \
  -e AWS_DEFAULT_REGION=ap-southeast-2 \
  csdr-local sh -c "make product-gmw-v4-eez-process-geometry-s3"
```



test_read.py
```
import asyncio

import rustac

from csdr.io import (
    get_store_with_prefix_from_url,
    split_path_and_file_name_from_url,
)


def read_stacgeoparquet(dataset_url: str) -> None:
    print(f"rustac version: {rustac.version()}")
    print(f'duckdb version: {rustac.version("duckdb")}')
    print(f'stac-duckdb version: {rustac.version("stac-duckdb")}')
    print(f'stac-api version: {rustac.version("stac-api")}')
    print(f'stac version: {rustac.version("stac")}')


    path, file_name = split_path_and_file_name_from_url(dataset_url)
    store = get_store_with_prefix_from_url(path)

    async def test():
        items = await rustac.read(file_name, store=store)
        # print(items)
        print(f"Read {len(items['features'])} STAC items from {dataset_url}")
        return items

    stac_items = asyncio.run(test())
    

    # print(f"Found {len(stac_items)} STAC items")

read_stacgeoparquet("s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet")
read_stacgeoparquet("s3://csdr-public-dev/datasets/seagrass/0-0-1/dep_s2_seagrass.parquet")
```