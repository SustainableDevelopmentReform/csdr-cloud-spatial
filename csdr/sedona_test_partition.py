import sedona.db
from datetime import datetime

url = "s3://csdr-public-dev/datasets/aca/0-0-1/partition/"
# s3://csdr-public-dev/datasets/aca/0-0-1/partition/reefextent_0_3.parquet
region = "ap-southeast-2"

start_time = datetime.now()

sd = sedona.db.connect()

sd.read_parquet(url, options={"aws.skip_signature": True, "aws.region": region}).to_view("reef", overwrite=True)

total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
print(f"Total time taken: {total_seconds} seconds")

target_wkt = (
    # "POLYGON ((-73.21 44.03, -73.21 43.98, -73.11 43.97, -73.12 44.03, -73.21 44.03))"
    "POLYGON ((165.3811694224928 0.7514870407407557, 165.3811694224928 -2.100780109401157, 168.79148919598953 -2.100780109401157, 168.79148919598953 0.7514870407407557, 165.3811694224928 0.7514870407407557))"
)

start_time = datetime.now()
target_reef = sd.sql(f"""
SELECT * FROM reef
WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{target_wkt}'), 4326))
""").to_memtable()
total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
print(f"Total time taken: {total_seconds} seconds")
target_reef.show()
