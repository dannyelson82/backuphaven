"""Every bundled plugin restores its app (ADR-0013): fixture round trips."""

from pathlib import Path

import pytest

from backuphaven.plugins import fixture
from backuphaven.plugins.registry import bundled

FIXTURES = Path(__file__).parent / "plugin_fixtures"
PLUGINS = sorted(bundled().plugins)


@pytest.mark.parametrize("plugin_id", PLUGINS)
def test_plugin_restores_its_app(plugin_id: str) -> None:
    path = FIXTURES / f"{plugin_id}.yaml"
    assert path.exists(), f"every plugin needs a fixture: tests/plugin_fixtures/{plugin_id}.yaml"
    try:
        problems = fixture.round_trip(bundled().plugins[plugin_id], fixture.load(path))
    except fixture.DatabaseServerMissingError as exc:
        pytest.skip(str(exc))  # CI runs the database servers
    assert problems == []


def test_a_plugin_that_misses_a_file_is_caught(tmp_path: Path) -> None:
    sample = fixture.load(FIXTURES / "sonarr.yaml")
    broken = sample.model_copy(update={"must_restore": [*sample.must_restore, "/config/logs.db"]})
    problems = fixture.round_trip(bundled().plugins["sonarr"], broken)
    assert any("logs.db isn't in the backup" in p for p in problems)
    wrong_image = sample.model_copy(update={"image": "someone/else"})
    assert any(
        "recognise" in p for p in fixture.round_trip(bundled().plugins["sonarr"], wrong_image)
    )


def test_check_command(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from backuphaven.plugins.__main__ import main
    from backuphaven.plugins.registry import BUNDLED_DIR

    good = ["check", str(BUNDLED_DIR / "sonarr.yaml"), "--fixture", str(FIXTURES / "sonarr.yaml")]
    assert main(good) == 0
    assert "backed up, wiped and restored" in capsys.readouterr().out
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: Not Valid\n")
    assert main(["check", str(bad)]) == 1
    assert "✗" in capsys.readouterr().out
