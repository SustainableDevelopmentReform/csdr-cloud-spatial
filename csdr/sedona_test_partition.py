from datetime import datetime

import pandas as pd
import sedona.db

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
area_result = sd.sql(
    f"""
    SELECT SUM(ST_Area(ST_Transform(geometry, 6933))) AS total_area
    FROM reef
    WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{target_wkt}'), 4326))
    """
).to_pandas()

val = area_result['total_area'][0]
if pd.isna(val):
    print("No intersected reef geometries found.")
else:
    print(f"Total intersected area: {val:.2f} m^2")

total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
print(f"Total time taken: {total_seconds} seconds")
