from pathlib import Path

import pytest

from distro_recovery.backends.base import FixtureRunner

FIXTURES = Path(__file__).parent / "fixtures" / "dnf5"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def runner() -> FixtureRunner:
    return FixtureRunner(FIXTURES)
