import logging
import os
from datetime import datetime
from json import dumps
from typing import Literal

import typer

from csdr.app_integration import post_workflow_status
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


@helpers_app.command("update-workflow-db-status")
def update_workflow_db_status(
    workflow_id: str = typer.Option(..., help="Workflow ID to update in the database."),
    status: Literal["Succeeded", "Failed", "Error"] = typer.Option(
        ..., help="New status to set for the workflow run in the database."
    ),
) -> None:
    logging.info(
        f"Updating workflow run {workflow_id} to status '{status}' in the database..."
    )

    content = {
        "workflowId": workflow_id,
        "status": status,
    }
    response = post_workflow_status(content)
    try:
        response.raise_for_status()
    except Exception as e:
        logging.exception(
            f"Failed to update workflow's status in database.\nError: {e}\nResponse was: \n{dumps(response.json(), indent=2)}",
        )
        raise
    else:
        logging.info(
            f"Successfully updated workflow's status in database: {response.status_code}"
        )
