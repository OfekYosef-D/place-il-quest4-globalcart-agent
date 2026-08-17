"""Minimal smoke test so a pytest run is never 'green' with zero tests."""

import app


def test_app_package_importable() -> None:
    assert app.__version__ == "0.1.0"
