# docker build -t csdr-local .
# docker run --platform linux/amd64 --rm  891612567384.dkr.ecr.ap-southeast-2.amazonaws.com/csdr/csdr-cloud-spatial:latest
docker run --rm -it \
  -e AWS_ACCESS_KEY_ID=replace-me \
  -e AWS_SECRET_ACCESS_KEY=replace-me \
  -e AWS_DEFAULT_REGION=ap-southeast-2 \
  -e AWS_SESSION_TOKEN=replace-me \
  csdr-local python -c """
from csdr.utils import search_stacgeoparquet
from odc.geo.geom import Geometry
import os
import logging
logging.basicConfig(level=logging.DEBUG)

print('AWS Environment Variables:')
print(f'AWS_ACCESS_KEY_ID: {os.getenv(\"AWS_ACCESS_KEY_ID\")}')
print(f'AWS_SECRET_ACCESS_KEY: {os.getenv(\"AWS_SECRET_ACCESS_KEY\")}')
print(f'AWS_DEFAULT_REGION: {os.getenv(\"AWS_DEFAULT_REGION\")}')

geom = Geometry({
  \"coordinates\": [
    [
      [
        164.13996974784516,
        2.1246315445349495
      ],
      [
        164.13996974784516,
        -3.2715193876831847
      ],
      [
        169.74320380223287,
        -3.2715193876831847
      ],
      [
        169.74320380223287,
        2.1246315445349495
      ],
      [
        164.13996974784516,
        2.1246315445349495
      ]
    ]
  ],
  \"type\": \"Polygon\"
}, crs=\"EPSG:4326\")
items = search_stacgeoparquet('s3://csdr-public-dev/datasets/seagrass/0-0-1/dep_s2_seagrass.parquet', geometry=geom, datetime_string_match=\"2023\")
print(f'Found {len(items)} items')
"""


# # The issue seems to be the binary data type in the parquet file. Although it worked locally on the same file.



Here are some similar sounding issues:
https://github.com/apache/datafusion/issues/83#issuecomment-1497323407
https://github.com/apache/arrow-rs/issues/3373







PROBLEM 2: Testing the parquet loader.

# Parquet error in test. Invalid byte order.
# Validate using ogr2ogr:
# python3 validate_geoparquet.py --check-data tests/data/gmw/gmw.parquet


