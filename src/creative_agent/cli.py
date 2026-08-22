"""Composition root and CLI. The Claude SDK is imported lazily here only."""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from creative_agent import __version__
from creative_agent.config import HarnessSettings
from creative_agent.errors import CreativeAgentError, ExitCode
from creative_agent.harness.oracle import OracleLoader

app = typer.Typer(name="creative-agent", no_args_is_help=True, add_completion=False)
oracles_app = typer.Typer(no_args_is_help=True)
app.add_typer(oracles_app, name="oracles", help="Inspect and validate oracle data files.")


def _settings() -> HarnessSettings:
    return HarnessSettings()


def _loader(settings: HarnessSettings) -> OracleLoader:
    return OracleLoader(settings.oracle_search_paths, settings.max_oracle_bytes)


def _fail(exc: CreativeAgentError) -> None:
    typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(code=int(exc.exit_code))


@app.callback()
def _main() -> None:
    """creative-agent: doctrine-driven review harness."""


@app.command()
def version() -> None:
    """Print the harness version."""
    typer.echo(__version__)


@oracles_app.command("list")
def oracles_list() -> None:
    """List discoverable oracles (search paths first, packaged data last)."""
    try:
        tables = _loader(_settings()).load_all()
    except CreativeAgentError as exc:
        _fail(exc)
    for oracle_id in sorted(tables):
        table = tables[oracle_id]
        typer.echo(f"{oracle_id}\tv{table.version}\trows={len(table.rows)}\t{table.name}")


@oracles_app.command("validate")
def oracles_validate(
    name: Annotated[str | None, typer.Argument(help="Oracle id to validate")] = None,
    all_oracles: Annotated[bool, typer.Option("--all", help="Validate every oracle")] = False,
) -> None:
    """Validate oracle data files against the schema (used by CI)."""
    settings = _settings()
    loader = _loader(settings)
    try:
        if all_oracles:
            tables = loader.load_all()
            if not tables:
                raise CreativeAgentError("no oracle files found")
            for oracle_id in sorted(tables):
                typer.echo(f"ok: {oracle_id} (rows={len(tables[oracle_id].rows)})")
        elif name:
            table = loader.load(name)
            typer.echo(f"ok: {table.oracle_id} (rows={len(table.rows)})")
        else:
            typer.echo("error: pass an oracle id or --all", err=True)
            raise typer.Exit(code=int(ExitCode.CONFIG_ERROR))
    except CreativeAgentError as exc:
        _fail(exc)


def main() -> int:
    """Console entry point wrapper mapping unexpected errors to exit code 5."""
    try:
        # click returns the Exit code (rather than raising) when standalone mode is off.
        return_value = app(standalone_mode=False)
    except typer.Exit as exc:
        return exc.exit_code
    except typer.Abort:
        return int(ExitCode.UNEXPECTED_ERROR)
    except CreativeAgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    except Exception as exc:  # the exit-code contract demands a defined code for anything
        print(f"unexpected error: {exc}", file=sys.stderr)
        return int(ExitCode.UNEXPECTED_ERROR)
    if isinstance(return_value, int):
        return return_value
    return int(ExitCode.CLEAN)


if __name__ == "__main__":
    sys.exit(main())
