import json as json_lib

import typer
from dotenv import load_dotenv

from client import _client

load_dotenv()

app = typer.Typer(help="Probe tool — minimal liveness check.")


@app.command()
def ping(
    json: bool = typer.Option(False, "--json", help="Emit JSON."),
    markdown: bool = typer.Option(False, "--markdown", help="Emit markdown."),
) -> None:
    result = _client().ping()
    if json:
        typer.echo(json_lib.dumps({"result": result}))
    elif markdown:
        typer.echo(f"**probe.ping** → `{result}`")
    else:
        typer.echo(result)


if __name__ == "__main__":
    app()
