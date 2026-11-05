"""CLI status uses configuration without creating or modifying data."""

import json
from pathlib import Path

import pytest

from weather_pipeline.cli import main
from weather_pipeline.config import Settings


def test_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("{}")
    assert main(["--config", str(config), "status"]) == 1
    event = json.loads(capsys.readouterr().err)
    assert json.loads(event["message"])["status"] == "not_initialized"
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


@pytest.mark.parametrize("args", [["run", "--limit-cities", "0"], ["status", "--city", "Pune"]])
def test_bad_filters(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2


def test_all_commands(
    settings: "Settings",
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import Mock

    config = tmp_path / "config.yaml"
    config.write_text(settings.model_dump_json())
    assert main(["--config", str(config), "backfill"]) == 0
    assert main(["--config", str(config), "validate"]) == 0
    assert main(["--config", str(config), "status"]) == 0
    assert main(["--config", str(config), "export-csv"]) == 0
    mocked = Mock()
    mocked.run.return_value = {"status": "partial"}
    monkeypatch.setattr("weather_pipeline.cli.Pipeline", Mock(return_value=mocked))
    assert main(["--config", str(config), "run", "--city", "Pune"]) == 1
    mocked.run.side_effect = RuntimeError("failed")
    assert main(["--config", str(config), "run"]) == 2
    capsys.readouterr()
