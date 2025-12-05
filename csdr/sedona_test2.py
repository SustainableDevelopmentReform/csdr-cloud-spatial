import sedona.db
from datetime import datetime

# url = "s3://overturemaps-us-west-2/release/2025-11-19.0/theme=buildings/type=building/"
url = "https://csdr-public-dev.s3.ap-southeast-2.amazonaws.com/datasets/aca/0-0-1/reefextent.parquet"
# region = "us-west-2"
region = "ap-southeast-2"

start_time = datetime.now()

sd = sedona.db.connect()

sd.read_parquet(url, options={"aws.skip_signature": True, "aws.region": region}).to_view("buildings")

total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
print(f"Total time taken: {total_seconds} seconds")

target_wkt = (
    # "POLYGON ((-73.21 44.03, -73.21 43.98, -73.11 43.97, -73.12 44.03, -73.21 44.03))"
    "POLYGON ((165.3811694224928 0.7514870407407557, 165.3811694224928 -2.100780109401157, 168.79148919598953 -2.100780109401157, 168.79148919598953 0.7514870407407557, 165.3811694224928 0.7514870407407557))"
)

start_time = datetime.now()
target_buildings = sd.sql(f"""
SELECT * FROM buildings
WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{target_wkt}'), 4326))
""").to_memtable()
total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
print(f"Total time taken: {total_seconds} seconds")
target_buildings.show()

# sd.read_parquet("s3://overturemaps-us-west-2/release/2025-11-19.0//theme=divisions/type=division_area/").to_view("areas")
# sd.read_parquet(f"{PREFIX}/theme=buildings/type=building/").to_view("buildings")

# sd.sql(
#     """
# SELECT b.*
# FROM buildings b
# JOIN areas a
#   ON a.country IN ('AS','CK','FM','FJ','PF','GU','KI','MH','NR','NC','NU','MP','PW','PG','PN','WS','SB','TK','TO','TV','VU')
#  AND ST_Intersects(b.geometry, a.geometry)
# """
# ).to_pandas().to_crs(3832).to_file("dep_overture_buildings.gpkg")