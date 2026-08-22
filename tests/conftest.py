from __future__ import annotations

from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent / "golden"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="Rewrite golden files with current output instead of comparing.",
    )


class GoldenComparer:
    def __init__(self, update: bool) -> None:
        self._update = update

    def check(self, name: str, actual: str) -> None:
        path = GOLDEN_DIR / name
        if self._update:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(actual, encoding="utf-8", newline="\n")
            return
        assert path.exists(), f"golden file {name} missing — run with --update-goldens"
        expected = path.read_text(encoding="utf-8")
        assert actual == expected, (
            f"golden mismatch for {name}; run pytest --update-goldens if intentional"
        )


@pytest.fixture()
def golden(request: pytest.FixtureRequest) -> GoldenComparer:
    return GoldenComparer(request.config.getoption("--update-goldens"))
