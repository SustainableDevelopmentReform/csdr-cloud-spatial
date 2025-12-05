from pyarrow import dataset
from datetime import datetime

start_time = datetime.now()
ds = dataset.dataset("https://csdr-public-dev.s3.ap-southeast-2.amazonaws.com/geometries/testing/eez-v4/")
ds_fragments = list(ds.get_fragments())
len(ds_fragments)
total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
print(f"Total time taken: {total_seconds} seconds")

target_buildings = sd.sql(f"""
SELECT * FROM buildings
WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{target_wkt}'), 4326))
""").to_memtable()