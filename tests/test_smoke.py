from typer.testing import CliRunner

from distro_recovery.cli import app


def test_cli_help():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
