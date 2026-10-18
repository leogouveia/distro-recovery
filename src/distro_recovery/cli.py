import typer

app = typer.Typer(help="Inventário e restauração de aplicativos instalados.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """distro-recovery."""
