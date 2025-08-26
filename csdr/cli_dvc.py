import json
import logging
import os
import re
from typing import Annotated

import geopandas as gpd
import typer
import yaml
from dvc.exceptions import NotDvcRepoError
from dvc.repo import Repo
from dvc.stage import PipelineStage
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from csdr.utils import run_command

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class ShapelyEncoder(json.JSONEncoder):
    def default(self, obj: BaseGeometry | object) -> object:
        if isinstance(obj, BaseGeometry):
            return mapping(obj)
        return super().default(obj)


dvc_app = typer.Typer()


@dvc_app.command("status")
def status(
    pipeline_type: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Filter by pipeline type (datasets, geometries, products). "
                "Checks all if not specified."
            )
        ),
    ] = None,
    allow_missing: bool = typer.Option(
        False,
        "--allow-missing",
        help=(
            "Allow missing data. Useful if using existing intermediary "
            "datasets/geometries and only want to compute products."
        ),
    ),
) -> None:
    """Prints the status of the DVC repo."""
    logger.info("Checking status of DVC repo...")
    try:
        repo: Repo = Repo(uninitialized=False)

        reproduce_kwargs = {
            "dry": True,
            "on_error": "ignore",
            "allow_missing": allow_missing,
            "recursive": True,
        }

        if pipeline_type:
            if pipeline_type in ["datasets", "geometries", "products"]:
                logger.info(f"Filtering for pipeline type: {pipeline_type}")
                reproduce_kwargs["targets"] = [f"{pipeline_type}/"]
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

        if changed_pipelines:
            logger.info(f"Changed pipelines: {changed_pipelines}")
        else:
            logger.info("No changes detected.")

    except NotDvcRepoError:
        logger.error("Current directory is not a DVC repository.")
        raise typer.Exit(code=1)
    except Exception as e:
        logger.exception(f"Failed to get DVC status: {e}")
        raise typer.Exit(code=1)


# This isn't really needed at the moment, just use `dvc repro`

# @dvc_app.command("repro")
# def repro(
#     pipeline_type: Annotated[
#         Optional[str],
#         typer.Argument(
#             help=(
#                 "Filter by pipeline type (datasets, geometries, products). "
#                 "Checks all if not specified."
#             )
#         ),
#     ] = None,
#     allow_missing: bool = typer.Option(
#         False,
#         "--allow-missing",
#         help=(
#             "Allow missing data. Useful if using existing intermediary "
#             "datasets/geometries and only want to compute products."
#         ),
#     ),
# ):
#     """Reproduces the pipelines."""
#     logger.info("Reproducing pipelines...")
#     try:
#         repo: Repo = Repo(uninitialized=False)

#         reproduce_kwargs = {"recursive": True, "allow_missing": allow_missing}

#         if pipeline_type:
#             if pipeline_type in ["datasets", "geometries", "products"]:
#                 logger.info(f"Filtering for pipeline type: {pipeline_type}")
#                 reproduce_kwargs["targets"] = [f"{pipeline_type}/"]
#             else:
#                 logger.error(
#                     f"Invalid pipeline type: {pipeline_type}. "
#                     "Choose from 'datasets', 'geometries', 'products'."
#                 )
#                 raise typer.Exit(code=1)
#         else:
#             logger.info("Checking all pipelines.")
#             reproduce_kwargs["all_pipelines"] = True

#         repo.reproduce(**reproduce_kwargs)

#         logger.info("Reproduction complete.")

#     except NotDvcRepoError:
#         logger.error("Current directory is not a DVC repository.")
#         raise typer.Exit(code=1)
#     except Exception as e:
#         logger.error(f"Failed to get DVC status: {e}")
#         raise typer.Exit(code=1)


def commit_if_changes(message: str) -> None:
    logger.info("Checking Git status...")
    git_status_success, git_status_output, _ = run_command(
        ["git", "status", "--porcelain"]
    )
    if not git_status_success:
        logger.error("Failed to check Git status. Is this a Git repository?")
        raise typer.Exit(code=1)

    if bool(git_status_output):
        logger.info("Changes detected or commit forced. Staging and committing...")
        add_success, _, add_stderr = run_command(["git", "add", "."])
        if not add_success:
            logger.error(f"Failed to stage changes with 'git add .': {add_stderr}")
            raise typer.Exit(code=1)

        commit_cmd = ["git", "commit", "-m", message]
        commit_success, _, commit_stderr = run_command(commit_cmd)
        if not commit_success:
            logger.error(f"Failed to commit changes: {commit_stderr}")
            raise typer.Exit(code=1)
        else:
            logger.info(f"Committed changes with message: '{message}'")
    else:
        logger.info("No changes detected. Skipping commit.")


def generate_product_json_files(
    pipeline_base_path: str, product_out_path: str, pipeline_provenance_data: dict
) -> None:
    product_name = pipeline_base_path.split("/")[1]

    logger.info(
        f"Generating product JSON files for: {pipeline_base_path}, {product_out_path}"
    )
    try:
        product_gdf = gpd.read_parquet(
            product_out_path,
        )

        product_json_outputs_path = f"outputs/{pipeline_base_path}/"
        os.makedirs(product_json_outputs_path, exist_ok=True)

        # Rm existing JSON files in the output directory
        print(f"Removing existing JSON files in {product_json_outputs_path}")
        for file in os.listdir(product_json_outputs_path):
            if file.endswith(".geojson"):
                os.remove(os.path.join(product_json_outputs_path, file))

        features = product_gdf.iterfeatures()

        for i, feature in enumerate(features):
            feature["properties"]["provenance"] = pipeline_provenance_data

            # TODO: get geometry_name_dim from params.yaml in geometries pipeline, and then remove this from the feature properties (as we are getting duplicate geometry)
            # geometry_name_dim = pipeline_provenance_data.get(
            #     "params", {}).get("geometry_name_dim")
            # # Delete geometry_name_dim in properties if it exists
            # if geometry_name_dim:
            #     del feature["properties"][geometry_name_dim]

            name = f"product_{i}"
            if "name" in feature["properties"]:
                name = feature["properties"]["name"]

            # make name lowercase camelcase, and add pipeline name prefix
            name = (
                product_name
                + "_"
                + "_".join(
                    word.lower()
                    for word in re.sub(r"[^a-zA-Z0-9\s]", "", name).split(" ")
                    if word
                )
            )

            # Make sure we don't have any geometry column in the properties
            if "geometry" in feature["properties"]:
                del feature["properties"]["geometry"]

            print(f"Writing feature to {name}.geojson")
            with open(f"{product_json_outputs_path}{name}.geojson", "w") as f:
                json.dump(feature, f, cls=ShapelyEncoder, indent=4)

    except Exception as e:
        logger.exception(f"Failed to generate product JSON files: {e}")
        raise typer.Exit(code=1)


@dvc_app.command("publish")
def publish(
    pre_commit_message: str = typer.Option(
        "Automated commit before publishing provenance",
        "--pre-commit-message",
        "-p",
        help="Git commit message if changes are detected.",
    ),
    post_commit_message: str = typer.Option(
        "Automated commit after publishing provenance",
        "--post-commit-message",
        "-c",
        help="Git commit message if changes are detected.",
    ),
    no_commit: bool = typer.Option(
        False,
        "--no-commit",
        help="Do not commit changes to Git.",
    ),
    base_url: str = typer.Option(
        "https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial/blob/{commit_hash}/",
        "--base-url",
        "-b",
        help="Base URL for git repository, with {commit_hash} placeholder for the commit hash.",
    ),
) -> None:
    """
    Generates provenance JSON files for DVC pipelines after ensuring the
    repository state is captured in Git.
    """
    logger.info("Starting provenance generation...")

    if not no_commit:
        commit_if_changes(pre_commit_message)
    else:
        logger.info("Skipping Git commit - --no-commit flag used.")

    try:
        logger.info("Initializing DVC repo object...")
        dvc_repo: Repo = Repo(uninitialized=False)

        logger.info("Collecting DVC stages...")
        all_stages = list(dvc_repo.index.stages)

        if not all_stages:
            logger.warning("No DVC stages found in the repository.")
            return

        # Group stages by their pipeline file (dvc.yaml path)
        pipelines: dict[str, list[PipelineStage]] = {}
        for stage in all_stages:
            if stage.relpath not in pipelines:
                pipelines[stage.relpath] = []
            pipelines[stage.relpath].append(stage)

        logger.info(f"Found {len(pipelines)} DVC pipeline files.")

        for pipeline_path, stages_in_pipeline in pipelines.items():
            pipeline_base_path = pipeline_path.replace("dvc.yaml", "")
            provenance_file_path = pipeline_path.replace("dvc.yaml", "provenance.json")

            logger.info(
                f"Processing pipeline: {pipeline_path} -> {provenance_file_path}"
            )

            # Get git commit has for dvc.lock file
            dvc_lock_path = pipeline_path.replace("dvc.yaml", "dvc.lock")

            if not os.path.exists(dvc_lock_path):
                logger.warning(f"DVC lock file not found for pipeline: {pipeline_path}")
                continue

            # Call git command to get commit hash for dvc.lock file
            commit_hash_success, dvc_lock_commit_hash, commit_hash_stderr = run_command(
                ["git", "rev-list", "-1", "HEAD", "--", dvc_lock_path]
            )
            if not commit_hash_success:
                logger.error(
                    f"Failed to get git commit hash for pipeline: {pipeline_path} - {commit_hash_stderr}"
                )
                continue

            # Call git command to get commit date for dvc.lock file
            commit_date_success, dvc_lock_commit_date, commit_date_stderr = run_command(
                [
                    "git",
                    "show",
                    "-s",
                    "--format=%cd",
                    "--date=iso",
                    dvc_lock_commit_hash,
                ]
            )
            if not commit_date_success:
                logger.error(
                    f"Failed to get git commit date for pipeline: {pipeline_path} - {commit_date_stderr}"
                )
                continue

            # Get params.yaml file for the pipeline
            params_file_path = pipeline_path.replace("dvc.yaml", "params.yaml")
            if os.path.exists(params_file_path):
                with open(params_file_path) as f:
                    params = yaml.safe_load(f)
            else:
                logger.warning(f"Params file not found for pipeline: {pipeline_path}")
                params = {}

            base_url_with_commit_hash = base_url.format(
                commit_hash=dvc_lock_commit_hash
            )

            # Initialize aggregated provenance data for the pipeline
            pipeline_provenance_data = {
                "pipeline_file": base_url_with_commit_hash + pipeline_path,
                "lock_file": base_url_with_commit_hash + dvc_lock_path,
                "params": params,
                "git_commit": dvc_lock_commit_hash,
                "git_commit_date": dvc_lock_commit_date,
                "dependencies": [],
                # TODO add docker info (if running in container)
                #
            }

            # Aggregate info from each stage within the pipeline
            for stage in stages_in_pipeline:
                stage_name = stage.name or "<default>"
                logger.debug(f"  Processing stage: {stage_name}")

                # Get all stages that depend on the current stage
                for dep_stage in dvc_repo.index.graph.successors(stage):
                    # Add to dependencies, if it is not already in
                    if (
                        dep_stage.relpath != pipeline_path
                        and dep_stage.relpath
                        not in pipeline_provenance_data["dependencies"]
                    ):
                        pipeline_provenance_data["dependencies"].append(
                            base_url_with_commit_hash + dep_stage.relpath
                        )

            # Write the aggregated JSON file for the pipeline
            try:
                with open(provenance_file_path, "w") as f:
                    json.dump(pipeline_provenance_data, f, indent=4)
                    f.write("\n")
                logger.info(
                    f"Successfully generated provenance file: {provenance_file_path}"
                )
            except OSError as e:
                logger.exception(
                    f"Failed to write provenance file {provenance_file_path}: {e}"
                )
            except Exception as e:
                logger.exception(
                    f"An unexpected error occurred while writing "
                    f"{provenance_file_path}: {e}"
                )

            if pipeline_path.startswith("products/"):
                product_out_path = params.get("out_s3_path")

                # TODO only generate product JSON files if product has changed
                if product_out_path.endswith(".parquet"):
                    generate_product_json_files(
                        pipeline_base_path, product_out_path, pipeline_provenance_data
                    )
                else:
                    logger.info(
                        f"Skipping product JSON files for: {pipeline_path} - only parquet products are supported"
                    )

        logger.info("Provenance generation finished.")

        if not no_commit:
            commit_if_changes(post_commit_message)

            # Push to the remote
            logger.info("Pushing changes to remote repository...")
            push_success, push_output, push_stderr = run_command(["git", "push"])
            if not push_success:
                logger.error(f"Failed to push changes: {push_stderr}")
                raise typer.Exit(code=1)
            else:
                logger.info(f"Successfully pushed changes: {push_output}")
        else:
            logger.info("Skipping Git commit - --no-commit flag used.")

    except NotDvcRepoError:
        logger.error("Current directory is not a DVC repository.")
        raise typer.Exit(code=1)
    except Exception as e:
        logger.exception(f"Failed during DVC processing or provenance generation: {e} ")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    dvc_app()
