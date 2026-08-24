"""Smoke coverage for the local Stage 2 FastAPI demo shell."""

from pathlib import Path

import web_app


def test_web_demo_assets_and_routes_exist():
    web_dir = Path(web_app.WEB_DIR)
    assert (web_dir / "index.html").is_file()
    assert (web_dir / "styles.css").is_file()
    assert (web_dir / "app.js").is_file()
    paths = {route.path for route in web_app.app.routes}
    assert "/" in paths
    assert "/api/health" in paths
    assert "/api/scenarios" in paths
    assert "/api/run" in paths


def test_demo_catalog_covers_core_stage2_cases():
    ids = {scenario["id"] for scenario in web_app.SCENARIOS}
    assert ids == {
        "clean-refund",
        "high-risk-damaged",
        "missing-laptop",
        "identity-mismatch",
        "unknown-order",
    }
