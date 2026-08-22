"""Composition root and CLI. The Claude SDK is imported lazily here only."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from creative_agent import __version__
from creative_agent.config import HarnessSettings
from creative_agent.errors import CreativeAgentError, ExitCode
from creative_agent.harness.oracle import OracleLoader

app = typer.Typer(name="creative-agent", no_args_is_help=True, add_completion=False)
oracles_app = typer.Typer(no_args_is_help=True)
app.add_typer(oracles_app, name="oracles", help="Inspect and validate oracle data files.")
agents_app = typer.Typer(no_args_is_help=True)
app.add_typer(agents_app, name="agents", help="List registered review agents.")
decisions_app = typer.Typer(no_args_is_help=True)
app.add_typer(decisions_app, name="decisions", help="Inspect CONFIRM-FIRST decision logs.")
state_app = typer.Typer(no_args_is_help=True)
app.add_typer(state_app, name="state", help="Inspect per-artifact review state.")


def _settings() -> HarnessSettings:
    return HarnessSettings()


def _loader(settings: HarnessSettings) -> OracleLoader:
    return OracleLoader(settings.oracle_search_paths, settings.max_oracle_bytes)


def _fail(exc: CreativeAgentError) -> NoReturn:
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


@oracles_app.command("rebaseline")
def oracles_rebaseline(
    name: Annotated[str, typer.Argument(help="Oracle id to re-baseline")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report resolutions without writing")
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option(help="Write the updated oracle here (default: the source file)"),
    ] = None,
) -> None:
    """Resolve every source against arXiv, diff author lists, bump freshness metadata.

    Note: rewriting the YAML drops hand-written comments; review the diff before
    committing, and log a decision-log entry for any invariant change (see CLAUDE.md).
    """
    import yaml as yaml_module

    from creative_agent.harness.citations import ArxivCitationResolver, OracleRebaseliner
    from creative_agent.harness.clock import SystemClock

    settings = _settings()
    try:
        source_path, table = _loader(settings).find(name)
    except CreativeAgentError as exc:
        _fail(exc)
    resolver = ArxivCitationResolver(settings.arxiv_api_url, settings.citation_timeout_seconds)
    rebaseliner = OracleRebaseliner(resolver, SystemClock())
    updated, report = asyncio.run(rebaseliner.rebaseline(table))
    for line in report:
        typer.echo(line)
    if dry_run:
        typer.echo("dry run: no file written")
        return
    target = output or source_path
    target.write_text(
        yaml_module.safe_dump(
            updated.model_dump(mode="json", exclude_none=True),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    typer.echo(f"wrote {target} (rebaseline_count={updated.freshness.rebaseline_count})")
    if any("MISMATCH" in line for line in report):
        raise typer.Exit(code=int(ExitCode.FINDINGS_MAJOR))


@app.command()
def review(
    artifact: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    oracle: Annotated[
        str | None, typer.Option(help="Oracle id (default: the agent's default oracle)")
    ] = None,
    agent: Annotated[str, typer.Option(help="Registered agent name")] = "sutton-review",
    mode: Annotated[
        str, typer.Option(help="auto | conformance | advisory (auto is fail-closed)")
    ] = "auto",
    artifact_id: Annotated[
        str | None, typer.Option("--artifact-id", help="Stable id for cycle tracking")
    ] = None,
    artifact_repo: Annotated[
        Path | None,
        typer.Option(
            "--artifact-repo",
            exists=True,
            file_okay=False,
            help="Reviewed artifact's repo (enables DEC-S decision gating)",
        ),
    ] = None,
    offline: Annotated[bool, typer.Option(help="Deterministic checks only; no LLM calls")] = False,
    output_json: Annotated[
        Path | None, typer.Option("--output-json", help="Also write the report as JSON")
    ] = None,
    reset_state: Annotated[
        bool, typer.Option("--reset-state", help="Discard prior cycle history first")
    ] = False,
) -> None:
    """Run a doctrine review of ARTIFACT and print the rendered report."""
    from creative_agent.agents import build_registry
    from creative_agent.harness.artifact import read_artifact, resolve_artifact_id
    from creative_agent.harness.clock import SystemClock
    from creative_agent.harness.pipeline import ReviewPipeline
    from creative_agent.harness.protocols import LLMClient
    from creative_agent.harness.state import FileStateStore
    from creative_agent.models.findings import Severity
    from creative_agent.models.review import ReviewRequest

    settings = _settings()
    try:
        if mode not in ("auto", "conformance", "advisory"):
            from creative_agent.errors import ConfigError

            raise ConfigError(f"invalid --mode {mode!r}") from None
        review_agent = build_registry().get(agent)
        oracle_id = oracle or review_agent.default_oracle()
        table = _loader(settings).load(oracle_id)
        text = read_artifact(artifact, settings.max_artifact_bytes)
        resolved_id = resolve_artifact_id(artifact, text, artifact_id)
        store = FileStateStore(settings.review_log_dir)
        if reset_state:
            store.reset(resolved_id)
        llm: LLMClient
        if offline:
            from creative_agent.harness.llm.offline import OfflineLLMClient

            llm = OfflineLLMClient()
        else:
            from creative_agent.harness.llm.claude_sdk import ClaudeSDKAdapter

            llm = ClaudeSDKAdapter(settings)
        pipeline = ReviewPipeline(
            agent=review_agent,
            oracle=table,
            llm=llm,
            settings=settings,
            state_store=store,
            clock=SystemClock(),
        )
        request = ReviewRequest(
            artifact_path=artifact,
            artifact_id=resolved_id,
            oracle_id=oracle_id,
            agent_name=agent,
            mode=mode,
            artifact_repo=artifact_repo,
            offline=offline,
        )
        outcome = asyncio.run(pipeline.run(request))
    except CreativeAgentError as exc:
        _fail(exc)

    typer.echo(outcome.rendered)
    if output_json is not None:
        output_json.write_text(outcome.report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    severities = [Severity.parse(f.severity) for f in outcome.result.findings]
    if outcome.result.escalation is not None or Severity.BLOCKER in severities:
        raise typer.Exit(code=int(ExitCode.BLOCKER_OR_STOP))
    if any(s >= Severity.MAJOR for s in severities):
        raise typer.Exit(code=int(ExitCode.FINDINGS_MAJOR))


@agents_app.command("list")
def agents_list() -> None:
    """List registered agents and their default oracles."""
    from creative_agent.agents import build_registry

    registry = build_registry()
    for name in registry.names():
        agent = registry.get(name)
        typer.echo(f"{name}\toracle={agent.default_oracle()}")


@decisions_app.command("check")
def decisions_check(
    repo: Annotated[Path, typer.Option("--repo", exists=True, file_okay=False)] = Path("."),
    oracle: Annotated[str, typer.Option(help="Oracle whose required decisions apply")] = "sutton",
) -> None:
    """Check a repo's decision log against an oracle's required decisions."""
    from creative_agent.harness.decisions import DecisionGate

    settings = _settings()
    try:
        table = _loader(settings).load(oracle)
    except CreativeAgentError as exc:
        _fail(exc)
    findings = DecisionGate(table, settings.decision_log_filename).check(repo)
    if not findings:
        typer.echo("ok: all required decisions are CONFIRMED (or none required)")
        return
    for finding in findings:
        typer.echo(f"[{finding.severity.name}] {finding.summary}")
    raise typer.Exit(code=int(ExitCode.FINDINGS_MAJOR))


@state_app.command("show")
def state_show(artifact_id: Annotated[str, typer.Argument()]) -> None:
    """Print the recorded cycle history for an artifact."""
    from creative_agent.harness.state import FileStateStore

    settings = _settings()
    try:
        state = FileStateStore(settings.review_log_dir).load(artifact_id)
    except CreativeAgentError as exc:
        _fail(exc)
    typer.echo(f"artifact: {state.artifact_id}  cycles: {state.cycle}")
    for cycle_record in state.history:
        typer.echo(
            f"  cycle {cycle_record.cycle} [{cycle_record.mode}] "
            f"{len(cycle_record.findings)} finding(s)"
        )
        for finding in cycle_record.findings:
            typer.echo(
                f"    {finding.key.render()} severity={finding.severity.name} "
                f"disposition={finding.disposition}"
            )


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
