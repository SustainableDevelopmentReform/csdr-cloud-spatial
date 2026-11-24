import json
import os
import shutil
from pathlib import Path

import pytest
import typer
from obstore.store import LocalStore, S3Store
from typer.testing import CliRunner

from csdr.cli_provenance import provenance_app
from csdr.io import write_json
from csdr.provenance import (
    PROVENANCE_STEPS_DIR,
    clear_steps,
    get_image_state,
    get_provenance,
    get_provenance_steps_dir,
    get_workflow_template_fields,
    read_steps,
    write_step,
)
from csdr.utils import CSDRException

PROVENANCE_DETAILS = {
    # "source_url": "https://example.com/source",
    # "source_metadata_url": "https://example.com/source/metadata",
    "data_url": "s3://example.com/file",
    "data_type": "stac-geoparquet",
}

TEST_GEOPARQUET_NAME = "example-global-test-geoparquet"


@pytest.fixture
def image_identity(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Image identity for one test, restored afterwards.

    Keeps the real identity when the test image sets it (Docker CI); otherwise
    supplies a fixture identity so local runs do not depend on the shell.
    """
    image_code = os.environ.get(
        "IMAGE_REPO", "https://example.test/toolkit/tree/fixture-commit/"
    )
    image_tag = os.environ.get("IMAGE_TAG", "local/toolkit:fixture")
    monkeypatch.setenv("IMAGE_REPO", image_code)
    monkeypatch.setenv("IMAGE_TAG", image_tag)
    return image_code, image_tag


def _test_provenance_fields(provenance: dict[str, str | int]) -> None:
    for key, value in PROVENANCE_DETAILS.items():
        key = "".join(
            part.capitalize() if i > 0 else part
            for i, part in enumerate(key.split("_"))
        )
        if key == "extraInfo":
            for extra_key, extra_value in value.items():
                assert provenance[extra_key] == extra_value
        else:
            assert provenance[key] == value


def test_write_provenance(
    local_testdata_obstore: LocalStore,
    s3_testdata_obstore: S3Store,
    geoparquet_relative: str,
    image_identity: tuple[str, str],
) -> None:
    """Test writing provenance information to both local and S3 stores"""
    provenance_dict = get_provenance(
        TEST_GEOPARQUET_NAME,
        local_testdata_obstore,
        geoparquet_relative,
        **PROVENANCE_DETAILS,
    )

    # Write to local store
    local_prov_path = geoparquet_relative.replace(".parquet", ".provenance.json")
    write_json(local_testdata_obstore, local_prov_path, provenance_dict)
    assert local_testdata_obstore.head(local_prov_path)["size"] > 0

    # Write to S3 store
    s3_prov_path = geoparquet_relative.replace(".parquet", ".provenance.json")
    write_json(s3_testdata_obstore, s3_prov_path, provenance_dict)
    assert s3_testdata_obstore.head(s3_prov_path)["size"] > 0


# Test getting provenance information from a local file
def test_local_dataset_provenance(
    local_testdata_obstore: LocalStore,
    geoparquet_relative: str,
    image_identity: tuple[str, str],
) -> None:
    """Test getting provenance information from a local file"""
    image_code, image_tag = image_identity

    provenance = get_provenance(
        TEST_GEOPARQUET_NAME,
        local_testdata_obstore,
        geoparquet_relative,
        **PROVENANCE_DETAILS,
    )

    assert isinstance(provenance["dataSize"], int) and provenance["dataSize"] == 15836
    assert isinstance(provenance["dataEtag"], str)
    assert provenance["imageCode"] == image_code
    assert provenance["imageTag"] == image_tag
    _test_provenance_fields(provenance)


# Test getting provenance information from a mocked S3 file
@pytest.mark.skip(
    "Skipping because moto doesn't set the bucket name in the store config"
)
def test_s3_dataset_provenance(
    s3_testdata_obstore: S3Store, geoparquet_relative: str
) -> None:
    """Test getting provenance information from a mocked S3 file"""

    provenance = get_provenance(
        TEST_GEOPARQUET_NAME,
        s3_testdata_obstore,
        geoparquet_relative,
        **PROVENANCE_DETAILS,
    )

    assert isinstance(provenance["dataSize"], int) and provenance["dataSize"] == 15836
    assert (
        isinstance(provenance["dataEtag"], str)
        and provenance["dataEtag"] == "255ab95f6888079345b27aa2e1547796"  # Why quoted?!
    )
    assert provenance["imageCode"] in [
        "not-set",
        "https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial/tree/fake-sha-commit/",
    ]
    assert provenance["imageTag"] in [
        "not-set",
        "https://fake-registry/csdr/csdr-cloud-spatial:test",
    ]

    _test_provenance_fields(provenance)


def test_get_image_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test getting provenance information from environment variables"""
    monkeypatch.setenv("IMAGE_REPO", "test-repo")
    monkeypatch.setenv("IMAGE_TAG", "abc-123")

    image_provenance = get_image_state()

    assert image_provenance["imageCode"] == "test-repo"
    assert image_provenance["imageTag"] == "abc-123"


def test_get_image_state_restores_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting identity inside one test must not leak into the next one."""
    monkeypatch.delenv("IMAGE_REPO", raising=False)
    monkeypatch.delenv("IMAGE_TAG", raising=False)
    with monkeypatch.context() as scoped:
        scoped.setenv("IMAGE_REPO", "test-repo")
        scoped.setenv("IMAGE_TAG", "abc-123")
        assert get_image_state()["imageTag"] == "abc-123"
    assert "IMAGE_REPO" not in os.environ
    assert "IMAGE_TAG" not in os.environ


def test_provenance_steps_dir_defaults_to_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CSDR_PROVENANCE_STEPS_DIR", raising=False)
    assert get_provenance_steps_dir() == PROVENANCE_STEPS_DIR
    monkeypatch.setenv("CSDR_PROVENANCE_STEPS_DIR", "")
    assert get_provenance_steps_dir() == PROVENANCE_STEPS_DIR


def test_provenance_steps_dir_is_configurable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steps_dir = tmp_path / "steps"
    monkeypatch.setenv("CSDR_PROVENANCE_STEPS_DIR", str(steps_dir))
    monkeypatch.setenv("IMAGE_REPO", "https://example.test/toolkit/tree/abc/")
    assert get_provenance_steps_dir() == steps_dir

    # The source function must live under csdr/ so that a permalink can be built.
    write_step("First step", source_function=get_image_state)
    write_step("Second step", source_function=get_image_state)

    assert sorted(p.name for p in steps_dir.iterdir()) == [
        "step-0000.json",
        "step-0001.json",
    ]
    assert [step["label"] for step in read_steps()] == ["First step", "Second step"]
    clear_steps()
    assert read_steps() == []


# Skip if on GitHub Actions
@pytest.mark.skip("Skipping because Docker isn't available most places")
def test_docker_image_state() -> None:
    """Test getting provenance information from the docker image"""
    import subprocess

    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "csdr-cloud-spatial:latest",
                "python3",
                "-c",
                "import os; print(os.getenv('IMAGE_REPO')); print(os.getenv('IMAGE_TAG'))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        output = result.stdout.strip().split("\n")
        assert len(output) == 2
        image_code, image_tag = output

        assert (
            image_code
            == "https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial/tree/unknown-commit/"
        )
        assert image_tag == "unknown"
    except FileNotFoundError:
        print("Docker not found, skipping docker image provenance test.")
    except subprocess.CalledProcessError as e:
        print(f"Error running docker command: {e}")
        print(f"Stdout: {e.stdout}")
        print(f"Stderr: {e.stderr}")
        assert False, "Docker command failed"
    except Exception as e:
        print(f"Unexpected error: {e}")
        assert False, "Unexpected error occurred"


WORKFLOW_TEMPLATE = {
    "workflowTemplateUrl": "s3://bucket/workflows/example/abc.yaml",
    "workflowTemplateKind": "argo-workflowtemplate",
    "workflowTemplateName": "example-template",
    "workflowTemplateDigest": "sha256:" + "d" * 64,
}


def _dataset_provenance_args(data: Path) -> list[str]:
    return [
        "dataset",
        "--id",
        "dataset-1",
        "--dataset-url",
        str(data),
        "--dataset-type",
        "stac-geoparquet",
        "--source-metadata-url",
        "https://example.test/metadata",
        "--workflow-dag",
        "[]",
    ]


def test_workflow_template_fields_are_optional() -> None:
    assert get_workflow_template_fields() == {}
    assert get_workflow_template_fields(None, None, None, None) == {}


def test_workflow_template_fields_round_trip() -> None:
    assert (
        get_workflow_template_fields(
            WORKFLOW_TEMPLATE["workflowTemplateUrl"],
            WORKFLOW_TEMPLATE["workflowTemplateKind"],
            WORKFLOW_TEMPLATE["workflowTemplateName"],
            WORKFLOW_TEMPLATE["workflowTemplateDigest"],
        )
        == WORKFLOW_TEMPLATE
    )
    # Only the fields that are set are returned.
    assert get_workflow_template_fields("https://example.test/w.sh", "shell") == {
        "workflowTemplateUrl": "https://example.test/w.sh",
        "workflowTemplateKind": "shell",
    }


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"digest": "sha256:" + "d" * 64}, "needs its URL"),
        ({"url": "http://example.test/w.yaml"}, "https:// or s3://"),
        ({"url": "https://user:pw@example.test/w.yaml"}, "https:// or s3://"),
        ({"url": "https://example.test/w.yaml?sig=1"}, "https:// or s3://"),
        ({"url": "s3://bucket/w.yaml", "kind": "helm"}, "kind"),
        ({"url": "s3://bucket/w.yaml", "name": " leading-space"}, "name"),
        ({"url": "s3://bucket/w.yaml", "digest": "md5:abc"}, "digest"),
    ],
)
def test_workflow_template_fields_are_validated(
    kwargs: dict[str, str], message: str
) -> None:
    with pytest.raises(CSDRException, match=message):
        get_workflow_template_fields(**kwargs)


def test_provenance_command_writes_workflow_template_fields(
    tmp_path: Path, image_identity: tuple[str, str]
) -> None:
    data = tmp_path / "gmw_new.parquet"
    shutil.copy(Path(__file__).parent / "data/gmw/gmw_new.parquet", data)

    result = CliRunner().invoke(
        provenance_app,
        [
            *_dataset_provenance_args(data),
            "--workflow-template-url",
            WORKFLOW_TEMPLATE["workflowTemplateUrl"],
            "--workflow-template-kind",
            WORKFLOW_TEMPLATE["workflowTemplateKind"],
            "--workflow-template-name",
            WORKFLOW_TEMPLATE["workflowTemplateName"],
            "--workflow-template-digest",
            WORKFLOW_TEMPLATE["workflowTemplateDigest"],
        ],
    )

    assert result.exit_code == 0, result.output
    written = json.loads((tmp_path / "gmw_new.parquet.provenance.json").read_text())
    assert {key: written[key] for key in WORKFLOW_TEMPLATE} == WORKFLOW_TEMPLATE


def test_provenance_command_without_workflow_template_omits_fields(
    tmp_path: Path, image_identity: tuple[str, str]
) -> None:
    data = tmp_path / "gmw_new.parquet"
    shutil.copy(Path(__file__).parent / "data/gmw/gmw_new.parquet", data)

    result = CliRunner().invoke(provenance_app, _dataset_provenance_args(data))

    assert result.exit_code == 0, result.output
    written = json.loads((tmp_path / "gmw_new.parquet.provenance.json").read_text())
    assert not set(WORKFLOW_TEMPLATE) & set(written)


def test_provenance_command_refuses_a_malformed_digest(
    tmp_path: Path, image_identity: tuple[str, str]
) -> None:
    data = tmp_path / "gmw_new.parquet"
    shutil.copy(Path(__file__).parent / "data/gmw/gmw_new.parquet", data)

    result = CliRunner().invoke(
        provenance_app,
        [
            *_dataset_provenance_args(data),
            "--workflow-template-url",
            WORKFLOW_TEMPLATE["workflowTemplateUrl"],
            "--workflow-template-digest",
            "md5:abc",
        ],
    )

    assert result.exit_code != 0
    assert not (tmp_path / "gmw_new.parquet.provenance.json").exists()


@pytest.mark.parametrize("command", ["dataset", "geometry", "product"])
def test_every_provenance_command_accepts_workflow_template_options(
    command: str,
) -> None:
    click_group = typer.main.get_command(provenance_app)
    options = {
        option
        for param in click_group.commands[command].params
        for option in param.opts
    }
    assert {
        "--workflow-template-url",
        "--workflow-template-kind",
        "--workflow-template-name",
        "--workflow-template-digest",
    } <= options
