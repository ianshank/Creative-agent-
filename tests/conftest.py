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


@pytest.fixture(autouse=True)
def isolate_review_state(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point review state at a temporary directory for every test.

    `review_log_dir` defaults to `docs/review-log`, resolved against the working
    directory — which under pytest is the repository itself. Any test invoking `review`
    without overriding it therefore wrote real state, real audit bundles, and a lock
    file into the developer's checkout, and appended a cycle on every run. The escaped
    state was committed and reached the pull request.

    Autouse rather than opt-in: the failure is silent, and a test that forgets this is
    exactly the test that causes it. A test that wants a specific location still just
    sets the variable itself — monkeypatch applies in definition order, so a later
    `setenv` in the test body wins.
    """
    monkeypatch.setenv("CREATIVE_AGENT_REVIEW_LOG_DIR", str(tmp_path_factory.mktemp("review-log")))
