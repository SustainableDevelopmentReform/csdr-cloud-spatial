from datetime import datetime
from json import dumps

import boto3
import typer

app = typer.Typer()


@app.command()
def hello(
    bucket: str = "csdr-data-dev",
    bucket_path: str | None = None,
):
    """Write a little json doc to the bucket

    Args:
        bucket (str): The name of the S3 bucket to write to.
        bucket_path (str | None): A path within the bucket to write to.

    Run with `csdr hello --bucket csdr-data-dev --bucket-path test`.
    """

    dictionary = {
        "hello": "world",
        "timestamp": datetime.now().isoformat(),
    }

    key = "hello.json"
    if bucket_path is not None:
        key = f"{bucket_path}/{key}"

    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=dumps(dictionary).encode("utf-8"),
    )
    typer.echo(f"Hello, {bucket}!")
    typer.echo(f"Path: {bucket_path}")
    typer.echo("Object written to bucket.")

    return


if __name__ == "__main__":
    app()
