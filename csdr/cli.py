from datetime import datetime
from json import dumps

import boto3
import typer

# Import the subcommand applications
from .cli_datasets import dataset_app
from .cli_dvc import dvc_app
from .cli_geometries import geometry_app
from .cli_vector_cube import vector_cube_app

app = typer.Typer()

# Add the subcommands
app.add_typer(dataset_app, name="datasets", help="Commands for processing datasets.")
app.add_typer(
    geometry_app, name="geometries", help="Commands for processing geometries."
)
app.add_typer(
    vector_cube_app,
    name="vector-cube",
    help="Commands for vector-cube operations like zonal statistics.",
)
app.add_typer(dvc_app, name="dvc", help="Commands for DVC operations.")


@app.command()
def hello(
    bucket: str = "csdr-data-dev",
    bucket_path: str | None = None,
) -> None:
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
