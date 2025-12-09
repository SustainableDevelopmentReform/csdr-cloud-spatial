docker run --platform linux/amd64 --rm  891612567384.dkr.ecr.ap-southeast-2.amazonaws.com/csdr/csdr-cloud-spatial:latest python -c """
from rustac import read
import boto3
from obstore.auth.boto3 import Boto3CredentialProvider
from obstore.store import HTTPStore, LocalStore, ObjectStore, S3Store, from_url
import asyncio

store = from_url(
    's3://csdr-public-dev/datasets/seagrass/0-0-1',
    credential_provider=Boto3CredentialProvider(
        session=boto3.Session(
            aws_access_key_id=\"replace-me\",
            aws_secret_access_key=\"replace-me\",
            region_name=\"ap-southeast-2\",
        )
    ),
    skip_signature=True,
)
file_name = 'dep_s2_seagrass.parquet'

async def test(file_name, store):
    # output = await read('s3://csdr-public-dev/datasets/seagrass/0-0-1/dep_s2_seagrass.parquet') # Doesn't work for another reason.
    # output = await read('tests/data/single_geometry.parquet') # Works.
    output = await read(file_name, store=store) # Doesn't work for the nested reason. rustac.RustacError: Json error: data type Binary not supported in nested map for json writer
    print(output)
    return output

result = asyncio.run(test(file_name, store))
print(result)
"""

# The issue seems to be the mix of asyncio, rustac, and the nested binary data type in the parquet file. Although it worked locally on the same file.