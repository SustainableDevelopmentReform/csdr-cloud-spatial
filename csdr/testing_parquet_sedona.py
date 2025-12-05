from datetime import datetime

import requests
import sedona.db

start_time = datetime.now()
sd = sedona.db.connect()

# # EEZ parquet is only 24 MB
# # This works:
# "https://files.auspatious.com/EEZ_land_union_v4_202410.parquet"
# "us-west-2"

# start_time = datetime.now()
# # This works which shows that a small file in our bucket can be read without credentials:
# sd.read_parquet(
#     "https://csdr-public-dev.s3.ap-southeast-2.amazonaws.com/geometries/testing/eez-v4/EEZ_land_union_v4_202410.parquet",
#     options={"aws.skip_signature": True, "aws.region": "ap-southeast-2"},
# ).to_view("eez")
# target_eezs = sd.sql(f"""
# SELECT * FROM eez
# WHERE "UNION" = 'Nauru'
# """).to_memtable()
# # This works but takes 15 seconds:
# target_eezs = sd.sql(f"""
# SELECT * FROM eez
# WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('POLYGON ((165.52158873158862 0.7565336916904357, 165.52158873158862 -2.2879479496098583, 168.5549585762643 -2.2879479496098583, 168.5549585762643 0.7565336916904357, 165.52158873158862 0.7565336916904357))'), 4326))
# """).to_memtable()
# print(f"Query took {datetime.now() - start_time} seconds")
# target_eezs.show()

# Because I can't load the S3 parquet directly to sedona due to timeout issues, I need to dowlnload it first, and then load that into sedona.
# Download the file locally
# url = "https://csdr-public-dev.s3.ap-southeast-2.amazonaws.com/datasets/aca/0-0-1/reefextent.parquet"
# local_path = "/tmp/reefextent.parquet"
# with requests.get(url, stream=True) as r:
#     r.raise_for_status()
#     with open(local_path, "wb") as f:
#         for chunk in r.iter_content(chunk_size=8192):
#             f.write(chunk)

# Reef Extent parquet is 484.7 MB. So 20x larger.
# We can read it in s3.
sd.read_parquet(
    # "https://csdr-public-dev.s3.ap-southeast-2.amazonaws.com/datasets/aca/0-0-1/reefextent.parquet",
    "/tmp/reefextent.parquet",
    # options={"aws.skip_signature": True, "aws.region": "ap-southeast-2"}, # aws.timeout doesn't exist
).to_view("reefextent")

# However this times out. Generic HTTP error: HTTP error: request or response body error.
target_reefextent = sd.sql("""
SELECT * FROM reefextent
WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('POLYGON ((165.52158873158862 0.7565336916904357, 165.52158873158862 -2.2879479496098583, 168.5549585762643 -2.2879479496098583, 168.5549585762643 0.7565336916904357, 165.52158873158862 0.7565336916904357))'), 4326))
""").to_memtable()
total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
print(f"Total time taken: {total_seconds} seconds")

target_reefextent.show()
