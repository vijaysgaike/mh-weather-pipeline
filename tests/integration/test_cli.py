"""CLI status uses configuration without creating or modifying data."""

import json
from pathlib import Path

import pytest

from weather_pipeline.cli import main


def test_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("{}")
    assert main(["--config", str(config), "status"]) == 0
    event = json.loads(capsys.readouterr().err)
    assert "M1 scaffold ready" in event["message"]
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("contents", [None, "bad json", '{"batch_size": 0}'])
def test_config_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    contents: str | None,
) -> None:
    path = tmp_path / "config.yaml"
    if contents is not None:
        path.write_text(contents)
    assert main(["--config", str(path), "status"]) == 2
    assert json.loads(capsys.readouterr().err)["level"] == "ERROR"


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "status" in capsys.readouterr().out


def test_invalid_configuration_values_are_not_logged(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text('{"api_key": "secret-test-value"}')
    assert main(["--config", str(path), "status"]) == 2
    assert "secret-test-value" not in capsys.readouterr().err
