from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from socialledger.cli import app
from socialledger.config import load_brands
from socialledger.schema import Platform

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and "socialledger" in result.output


def test_demo_report_and_status(isolated_home: Path) -> None:
    assert runner.invoke(app, ["demo"]).exit_code == 0
    result = runner.invoke(
        app, ["report", "--brand", "demo-brand", "--period", "monthly", "--json"]
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert "report.json" in out["files"] and Path(out["run_dir"]).is_dir()

    status = runner.invoke(app, ["status", "--offline", "--json"])
    rows = json.loads(status.output)
    assert any(r["brand"] == "demo-brand" for r in rows)


def test_report_rejects_bad_period() -> None:
    result = runner.invoke(app, ["report", "--brand", "x", "--period", "yearly"])
    assert result.exit_code != 0


def test_snapshot_with_nothing_configured_says_so(isolated_home: Path) -> None:
    runner.invoke(app, ["demo"])  # demo brands are never fetched
    result = runner.invoke(app, ["snapshot"])
    assert result.exit_code == 0 and "Nothing to fetch" in result.output


def test_init_wizard_csv_only_is_rerunnable(isolated_home: Path) -> None:
    # Brand name, then YouTube: yes -> csv; every other platform: no.
    answers = "acme-test\ny\ncsv\nn\nn\nn\nn\n"
    for _ in range(2):  # safe to run twice
        result = runner.invoke(app, ["init"], input=answers)
        assert result.exit_code == 0, result.output
    brands = load_brands()
    assert [b.name for b in brands.brands] == ["acme-test"]
    acct = brands.get("acme-test").account(Platform.YOUTUBE)
    assert acct is not None and acct.mode == "csv"
    assert "bring-your-own-credentials" in result.output


def test_init_rejects_invalid_brand_then_reprompts(isolated_home: Path) -> None:
    answers = "SOCIALLEDGER_LIVE=1 uv run pytest\nacme-test\nn\nn\nn\nn\nn\n"
    result = runner.invoke(app, ["init"], input=answers)
    assert result.exit_code == 0, result.output
    assert "not a valid brand name" in result.output
