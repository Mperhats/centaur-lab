"""Standalone CLI for the ``pdf`` tool.

Three commands matching the tool's two stages plus a combined entry:

    uv run python cli.py fetch <url> [--output paper.pdf]
    uv run python cli.py parse <path>
    uv run python cli.py fetch-and-parse <url> [--json]

``fetch`` and ``parse`` exercise each stage in isolation (useful when
debugging which stage is failing). ``fetch-and-parse`` exercises the
same envelope the agent sees.
"""

import json
import sys
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv
from rich.console import Console

# Put ``overlay/`` on sys.path so ``tools.pdf`` resolves as a
# namespace-package import — byte-identical to how the API pod sets up
# the ``tools.*`` namespace at startup. The tool itself has no
# centaur_sdk dependency, so the bootstrap is intentionally narrow.
_THIS_DIR = Path(__file__).resolve().parent
_OVERLAY_DIR = _THIS_DIR.parents[1]
if str(_OVERLAY_DIR) not in sys.path:
    sys.path.insert(0, str(_OVERLAY_DIR))

load_dotenv(find_dotenv(usecwd=True))

app = typer.Typer(name="pdf", help="Fetch and parse PDFs to Markdown.")
console = Console()


@app.command()
def fetch(
    url: str = typer.Argument(..., help="HTTPS URL to a PDF."),
    output: str = typer.Option(
        "", "--output", "-o", help="Write bytes here. Defaults to URL basename."
    ),
    timeout: float = typer.Option(60.0, "--timeout", help="HTTP timeout seconds."),
    max_mb: int = typer.Option(50, "--max-mb", help="Reject responses larger than this."),
) -> None:
    """Stream a PDF to disk."""
    from tools.pdf.fetch.http import PdfFetchError, download_pdf
    from tools.pdf.utils import derive_filename_from_url

    try:
        data, mime = download_pdf(
            url,
            timeout=timeout,
            max_bytes=max_mb * 1024 * 1024,
        )
    except PdfFetchError as exc:
        console.print(f"[red]fetch failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    output_path = Path(output) if output else Path(derive_filename_from_url(url))
    output_path.write_bytes(data)
    console.print(
        f"[green]ok[/green] wrote {len(data):,} bytes ({mime}) to [bold]{output_path}[/bold]"
    )


@app.command()
def parse(
    path: str = typer.Argument(..., help="Path to a local PDF file."),
    min_size: int = typer.Option(
        100, "--min-size", help="Minimum chars to accept a parser stage as successful."
    ),
) -> None:
    """Parse a local PDF file to Markdown on stdout."""
    from tools.pdf.parse.markdown import PdfParseError, parse_pdf

    pdf_path = Path(path)
    if not pdf_path.is_file():
        console.print(f"[red]not a file:[/red] {pdf_path}")
        raise typer.Exit(1)

    try:
        markdown, parser_used = parse_pdf(pdf_path.read_bytes(), min_size=min_size)
    except PdfParseError as exc:
        console.print(f"[red]parse failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[dim]parser: {parser_used} | chars: {len(markdown):,}[/dim]")
    typer.echo(markdown)


@app.command("fetch-and-parse")
def fetch_and_parse_cmd(
    url: str = typer.Argument(..., help="HTTPS URL to a PDF."),
    json_output: bool = typer.Option(
        False, "--json", help="Emit the envelope dict as JSON instead of just the Markdown."
    ),
    timeout: float = typer.Option(60.0, "--timeout", help="HTTP timeout seconds."),
    max_mb: int = typer.Option(50, "--max-mb", help="Reject responses larger than this."),
    min_size: int = typer.Option(100, "--min-size", help="Min chars to accept a parser stage."),
) -> None:
    """Download a PDF and parse it to Markdown in one call."""
    from tools.pdf.client import PdfClient

    result = PdfClient().fetch_and_parse(
        url,
        timeout=timeout,
        max_bytes=max_mb * 1024 * 1024,
        min_size=min_size,
    )

    if json_output:
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(0 if result.get("status") == "ok" else 1)

    if result.get("status") != "ok":
        console.print(f"[red]{result.get('stage', '?')} failed:[/red] {result.get('error')}")
        raise typer.Exit(1)

    console.print(
        f"[dim]parser: {result['parser_used']} | "
        f"chars: {result['char_count']:,} | "
        f"bytes: {result['size_bytes']:,} | "
        f"mime: {result['mime_type']}[/dim]"
    )
    typer.echo(result["markdown"])


if __name__ == "__main__":
    app()
