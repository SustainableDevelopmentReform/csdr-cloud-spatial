import rustac


def search_stacgeoparquet(dataset_url: str) -> None:
    client = rustac.DuckdbClient()
    aws_access_key_id="ASIA47GCAC5MNHEBRN6A"
    aws_secret_access_key="6tiPznAWCjWSxBGeq4mtkVeGC21fApD4P/GMwX5E"
    aws_session_token="IQoJb3JpZ2luX2VjEAEaCXVzLWVhc3QtMSJHMEUCIQCqhAnaGyMMUxEYCz76MZ9MF+pfX0diJ0eQeOLiUpq6dwIgFQfdXJyJXQyaqvRUH5PzEVnOoaBEb/M+GUc+eRXywfgqhwMIyf//////////ARAAGgw4OTE2MTI1NjczODQiDNylUNhWT3Qd/x88xCrbAimlifJq2D4IpyE4pJOjxUjpwmIGh7bdfvzisOW7DWkdDMul1lhbKRVx2DU2ms8yp6gnCPeKyFbmgrS+meslYDWwwh9rwUnvnuwek2W8mzBVfE7BfwhsSlvCpQCtnCpaoh4ttNYPbcYAmAmmgJKtG+n8mtH9YhZNvenBI5QlBtcDdGi+pJ3fPqO4OSPSnOfASIVomr+6H4GFYzudrPJRSUSEzm2DMlf5MbxqZvNdUj66mfssFk5lrbyY87zj7h4lVxywpTx5x9cR+kdff26k5Z+iU9lhijTrgOa9HqVL5R7M5CbdbT7mOovKvQA0oCWcBqiMkdRpbylW77is/fapEfFtL3RNV6cY3H/rUe1h0T5QR9mVdlHrK4VMcxQST6iuaE902sdaBPKWAVhLwlQXKheHPTDpH96+ZiHtFPonnvyRUokJ9Sq23Yrlh0u50vZMvyIpL+jPbJDTmyzaMKLy4skGOqUBNmppf4iK49ysIYl8QsXqxtVGMDbL8c4+BBHLMcbBb5sEEsG7+DicUoEfKFWZpXUIlRwBxb/GsCeluVhHDiROlnaqH/oXxDme9DrstKnXAI0WEKz9Ir3NQSQj95PUQJJ03hm0fLDQfNaMjyYCKaKrAMxCh7Wk6GEAeLrxsBmtCJw/YDQ3xozUvbLseLea5WJz8M9uksEFeSuWMITWzPR57Mk7gOw5"
    client.execute("INSTALL aws;")
    client.execute("LOAD aws;")
    client.execute("""
        CREATE OR REPLACE SECRET secret (
            TYPE s3,
            PROVIDER config,
            KEY_ID ?,
            SECRET ?,
            REGION 'ap-southeast-2',
            ENDPOINT 's3.ap-southeast-2.amazonaws.com',
            SESSION_TOKEN ?
        );
    """, params=[aws_access_key_id, aws_secret_access_key, aws_session_token])

    stac_items_search = client.search(dataset_url)
    print(f"Found {len(stac_items_search)} STAC items")
    # stac_items_read = await rustac.read(dataset_url)

search_stacgeoparquet("s3://csdr-public-dev/datasets/seagrass/0-0-1/dep_s2_seagrass.parquet")
