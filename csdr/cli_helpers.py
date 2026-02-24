import logging
import os
from datetime import datetime

import typer

from csdr.utils import make_uuid

helpers_app = typer.Typer()


@helpers_app.command("create-run-id")
def create_run_id() -> None:
    logging.info("Creating run ID...")

    now = datetime.now().isoformat()
    run_id = make_uuid(now)
    os.makedirs("/tmp", exist_ok=True)
    with open("/tmp/run_id.txt", "w") as f:
        f.write(run_id)
    logging.info(f"Run ID {run_id} written to /tmp/run_id.txt")
