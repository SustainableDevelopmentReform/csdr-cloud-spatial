import logging
import typer
from dvc.stage import PipelineStage
from dvc.repo import Repo
from dvc.exceptions import NotDvcRepoError
from typing import Optional
from typing_extensions import Annotated

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

dvc_app = typer.Typer()


@dvc_app.command("status")
def status(
    pipeline_type: Annotated[
        Optional[str],
        typer.Argument(
            help=(
                "Filter by pipeline type (datasets, geometries, products). "
                "Checks all if not specified."
            )
        ),
    ] = None,
):
    """Prints the status of the DVC repo."""
    logger.info(f"Checking status of DVC repo...")
    try:
        repo: Repo = Repo(uninitialized=False)

        targets = None
        reproduce_kwargs = {"dry": True, "on_error": "ignore",
                            "allow_missing": True, "recursive": True}

        if pipeline_type:
            if pipeline_type in ["datasets", "geometries", "products"]:
                logger.info(f"Filtering for pipeline type: {pipeline_type}")
                targets = [f"{pipeline_type}/"]
                reproduce_kwargs["targets"] = targets
            else:
                logger.error(
                    f"Invalid pipeline type: {pipeline_type}. "
                    "Choose from 'datasets', 'geometries', 'products'."
                )
                raise typer.Exit(code=1)
        else:
            logger.info("Checking all pipelines.")
            reproduce_kwargs["all_pipelines"] = True

        status_dict: list[PipelineStage] = repo.reproduce(**reproduce_kwargs)

        changed_pipelines = set()

        for stage in status_dict:
            logger.info(f"Changed: {stage.relpath}:{stage.name}")
            changed_pipelines.add(stage.relpath)

        logger.info(f"Changed pipelines: {changed_pipelines}")

    except NotDvcRepoError:
        logger.error("Current directory is not a DVC repository.")
        raise typer.Exit(code=1)
    except Exception as e:
        logger.error(f"Failed to get DVC status: {e}")
        raise typer.Exit(code=1)


@dvc_app.command("repro")
def repro(
    pipeline_type: Annotated[
        Optional[str],
        typer.Argument(
            help=(
                "Filter by pipeline type (datasets, geometries, products). "
                "Checks all if not specified."
            )
        ),
    ] = None,
    allow_missing: bool = typer.Option(
        False, "--allow-missing", help="Allow missing data. This is useful if you are using existing intermediary datasets/geometries and only want to compute the products."
    ),
):
    """Reproduces the pipelines."""
    logger.info(f"Reproducing pipelines...")
    try:
        repo: Repo = Repo(uninitialized=False)

        targets = None
        reproduce_kwargs = {"recursive": True, "allow_missing": allow_missing}

        if pipeline_type:
            if pipeline_type in ["datasets", "geometries", "products"]:
                logger.info(f"Filtering for pipeline type: {pipeline_type}")
                targets = [f"{pipeline_type}/"]
                reproduce_kwargs["targets"] = targets
            else:
                logger.error(
                    f"Invalid pipeline type: {pipeline_type}. "
                    "Choose from 'datasets', 'geometries', 'products'."
                )
                raise typer.Exit(code=1)
        else:
            logger.info("Checking all pipelines.")
            reproduce_kwargs["all_pipelines"] = True

        repo.reproduce(**reproduce_kwargs)

        logger.info("Reproduction complete.")

    except NotDvcRepoError:
        logger.error("Current directory is not a DVC repository.")
        raise typer.Exit(code=1)
    except Exception as e:
        logger.error(f"Failed to get DVC status: {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    dvc_app()
