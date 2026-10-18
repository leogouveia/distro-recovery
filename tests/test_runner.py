import pytest

from distro_recovery.backends.base import (
    CommandNotInFixtures,
    FixtureRunner,
    ReadOnlyViolation,
    SubprocessRunner,
)


def test_fixture_runner_returns_recorded_output(runner):
    res = runner(["rpm", "-q", "--whatprovides", "system-release"])
    assert res.returncode == 0
    assert res.stdout.strip() == "fedora-release-workstation-44-18.noarch"


def test_fixture_runner_unknown_command(runner):
    with pytest.raises(CommandNotInFixtures):
        runner(["dnf5", "--assumeno", "repoquery", "--nope"])


def test_fixture_runner_requires_manifest(tmp_path):
    with pytest.raises(FileNotFoundError):
        FixtureRunner(tmp_path)


@pytest.mark.parametrize(
    "argv",
    [
        ["sudo", "ls"],
        ["dnf5", "install", "vlc"],
        ["dnf5", "--assumeno", "remove", "vlc"],
        ["dnf5", "group", "install", "development-tools"],
        ["dnf5", "swap", "a", "b"],
        ["dnf5", "config-manager", "setopt", "x=1"],
        ["rpm", "-i", "x.rpm"],
        ["rpm", "-e", "vlc"],
        ["rpmkeys", "--import", "key"],
        ["rm", "-rf", "/"],
    ],
)
def test_subprocess_runner_blocks_mutating_commands(argv):
    with pytest.raises(ReadOnlyViolation):
        SubprocessRunner()(argv)


def test_subprocess_runner_runs_readonly_command(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("oi\n")
    res = SubprocessRunner()(["cat", str(f)])
    assert (res.returncode, res.stdout) == (0, "oi\n")
