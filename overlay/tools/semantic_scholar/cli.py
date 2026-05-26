"""Standalone CLI for the Semantic Scholar tool.

Used for local smoke tests without a Centaur sandbox. The CLI loads ``.env``
so ``SEMANTIC_SCHOLAR_API_KEY`` (if present) is honored; tool clients
running inside Centaur read the key via ``secret(...)`` from the manager
sidecar instead.

Run from this directory:

    uv run python cli.py search "diffusion models protein design" --limit 5
"""

import json
import sys
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv
from rich.console import Console

# Make `from centaur_sdk import ...` (used by client.py) resolvable when
# running from `uv run`. The upstream centaur_sdk pyproject uses
# `packages = ["."]`, which produces an editable install Python cannot
# import as a package — so we put the submodule's parent on sys.path
# instead. The API pod resolves the SDK normally via its own editable
# install, so this is a CLI-only workaround.
_THIS_DIR = Path(__file__).resolve().parent
_SDK_PARENT = _THIS_DIR.parents[2] / ".centaur"
if _SDK_PARENT.is_dir() and str(_SDK_PARENT) not in sys.path:
    sys.path.insert(0, str(_SDK_PARENT))
# Allow both `python cli.py ...` and `python -m semantic_scholar.cli`.
if str(_THIS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR.parent))
# Put `overlay/` on sys.path so `from shared.X import Y` (used by
# client.py for paper_document + metrics helpers) resolves under the
# CLI. The API pod gets this for free via the tool loader's bootstrap.
_OVERLAY_DIR = _THIS_DIR.parents[1]
if str(_OVERLAY_DIR) not in sys.path:
    sys.path.insert(0, str(_OVERLAY_DIR))

# Walk up from CWD to find a `.env`. The repo convention is one root .env
# (fed into the k8s Secret by `just bootstrap-secrets`); per-tool `.env`
# files are not consulted by the API. Doing usecwd=True still picks up a
# tool-local `.env` if you really want one for an isolated CLI session.
load_dotenv(find_dotenv(usecwd=True))

app = typer.Typer(name="semantic_scholar", help="Semantic Scholar Graph API CLI")
console = Console()


def _make_client():
    # Lazy-imported so the centaur_sdk path bootstrap above is in effect
    # before client.py tries `from centaur_sdk import secret`.
    from semantic_scholar.client import SemanticScholarClient

    return SemanticScholarClient()


def _format_authors(authors: list[dict] | None, max_authors: int = 3) -> str:
    if not authors:
        return ""
    names = [a.get("name", "") for a in authors if isinstance(a, dict)]
    names = [n for n in names if n]
    if len(names) > max_authors:
        return ", ".join(names[:max_authors]) + f" +{len(names) - max_authors}"
    return ", ".join(names)


def _truncate(text: str | None, length: int = 80) -> str:
    if not text:
        return ""
    if len(text) <= length:
        return text
    return text[: length - 1] + "…"


def _render_papers(papers: list[dict], title: str) -> None:
    # Lazy-imported so the centaur_sdk path bootstrap above is in effect
    # before `from centaur_sdk import Table` resolves. Mirrors the upstream
    # tool CLI convention of going through the SDK re-export instead of
    # importing rich.table.Table directly.
    from centaur_sdk import Table

    if not papers:
        console.print("[yellow]No results.[/]")
        raise typer.Exit()
    table = Table(title=title)
    table.add_column("Title", style="cyan", max_width=60)
    table.add_column("Authors", style="yellow", max_width=30)
    table.add_column("Year", style="green")
    table.add_column("Cites", style="dim", justify="right")
    for paper in papers:
        table.add_row(
            _truncate(paper.get("title"), 60),
            _format_authors(paper.get("authors"), max_authors=2),
            str(paper.get("year") or ""),
            str(paper.get("citationCount") or ""),
        )
    console.print(table)


@app.command()
def search(
    query: str = typer.Argument(..., help="Free-text search query"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results (1..100)"),
    year_from: int = typer.Option(None, "--year-from", "-y", help="Inclusive lower year bound"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Search papers by query."""
    with _make_client() as client:
        papers = client.search_papers(query=query, limit=limit, year_from=year_from)
    if json_output:
        print(json.dumps(papers, indent=2))
        return
    _render_papers(papers, title=f"Semantic Scholar: '{query}'")


@app.command()
def paper(
    paper_id: str = typer.Argument(..., help="Paper ID (S2, DOI:..., arXiv:...)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Fetch metadata for a single paper."""
    with _make_client() as client:
        data = client.get_paper(paper_id)
    if json_output:
        print(json.dumps(data, indent=2))
        return
    console.print(f"[bold cyan]{data.get('title', '(no title)')}[/]")
    console.print(f"[yellow]{_format_authors(data.get('authors'), max_authors=10)}[/]")
    console.print(f"Year: {data.get('year') or '?'}  |  Cites: {data.get('citationCount') or 0}")
    if data.get("url"):
        console.print(f"URL: {data['url']}")
    abstract = data.get("abstract") or ""
    if abstract:
        console.print()
        console.print(abstract)


@app.command()
def references(
    paper_id: str = typer.Argument(..., help="Paper ID (S2, DOI:..., arXiv:...)"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max references"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List the papers cited by the given paper."""
    with _make_client() as client:
        refs = client.get_references(paper_id=paper_id, limit=limit)
    if json_output:
        print(json.dumps(refs, indent=2))
        return
    _render_papers(refs, title=f"References of {paper_id}")


def _render_research_brief_summary(query: str, result: dict) -> None:
    # Lazy-imported so the centaur_sdk path bootstrap above is in effect
    # before ``from centaur_sdk import Table`` resolves. Mirrors the
    # convention used by ``_render_papers``.
    from centaur_sdk import Table

    table = Table(title=f"Research brief: '{query}'")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("status", str(result.get("status", "")))
    table.add_row("brief_document_id", str(result.get("brief_document_id", "")))
    table.add_row("brief_action", str(result.get("brief_action", "")))
    table.add_row("results_count", str(result.get("results_count", 0)))
    table.add_row("papers_inserted", str(result.get("papers_inserted", 0)))
    table.add_row("papers_updated", str(result.get("papers_updated", 0)))
    table.add_row("papers_noop", str(result.get("papers_noop", 0)))
    console.print(table)


@app.command("research-brief")
def research_brief_cmd(
    query: str = typer.Argument(..., help="The research topic to brief on."),
    limit: int = typer.Option(
        5, "--limit", "-n", help="Maximum number of papers to include in the brief."
    ),
    year_from: int | None = typer.Option(
        None,
        "--year-from",
        "-y",
        help="Restrict results to papers published from this year onward.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print the full result dict as JSON."
    ),
    pretty: bool = typer.Option(
        False, "--pretty", help="Print only the rendered Markdown brief."
    ),
) -> None:
    """Build and persist a research brief on a topic.

    Calls SemanticScholarClient.research_brief, which searches Semantic
    Scholar, renders a Markdown lit-review, and writes the brief plus
    its citing papers to company_context_documents for future RAG
    retrieval. Idempotent on (query, year_from) — re-running with the
    same inputs updates the existing rows in place.
    """
    # Mutual exclusion: ``--pretty`` strips everything but the markdown,
    # ``--json`` prints the full result dict — they describe two different
    # output modes, so accepting both would silently let one win and mask
    # the operator's intent. Mirror upstream's deep-research pattern of
    # explicit single-flag selection.
    if pretty and json_output:
        raise typer.BadParameter(
            "--pretty and --json are mutually exclusive; pick one."
        )

    with _make_client() as client:
        result = client.research_brief(query=query, limit=limit, year_from=year_from)

    if result.get("status") == "error":
        # The tool method's error envelope is the canonical place for
        # operator-actionable messages (empty query, missing DATABASE_URL,
        # S2 outage). Surface it verbatim and exit non-zero so callers
        # piping through ``glow`` or scripting around the CLI can branch
        # on exit code instead of parsing stdout.
        console.print(f"[red]research_brief failed:[/] {result.get('error', '')}")
        raise typer.Exit(1)

    if pretty:
        # Print the markdown only — useful for piping to ``glow`` or
        # pasting into Slack. Use ``print`` instead of ``console.print``
        # so Rich doesn't try to interpret embedded ``[brackets]`` in the
        # rendered brief as markup tags.
        print(result.get("markdown", ""))
        return

    if json_output:
        print(json.dumps(result, indent=2))
        return

    _render_research_brief_summary(query, result)


if __name__ == "__main__":
    app()
