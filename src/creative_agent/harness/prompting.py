"""PromptAssembler: oracle data + artifact → typed, schema-carrying calls.

Output schemas are always generated from the pydantic models (never hand-written), and
templates resolve across a search path with packaged defaults last — same override
discipline as oracles.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound
from pydantic import BaseModel

from creative_agent.errors import ConfigError
from creative_agent.harness.llm.base import AssembledPrompt, CallKind
from creative_agent.models.oracle import OracleRow


def output_model_for(kind: CallKind) -> type[BaseModel]:
    from creative_agent.models import sweeps

    mapping: dict[CallKind, type[BaseModel]] = {
        CallKind.CLASSIFY: sweeps.ClassifyResult,
        CallKind.ROW: sweeps.RowDisposition,
        CallKind.CLAIMS: sweeps.ClaimsResult,
        CallKind.SOURCE_QUALITY: sweeps.SourceQualityResult,
        CallKind.JUDGEMENT: sweeps.JudgementSweepResult,
        CallKind.SYNTHESIS: sweeps.SynthesisResult,
    }
    return mapping[kind]


def render_oracle_rows(rows: list[OracleRow]) -> str:
    """Serialize doctrine rows for prompt context."""
    lines = []
    for row in rows:
        source_bits = "; ".join(
            f"{s.citation}"
            + (f" [arXiv:{s.arxiv_id}]" if s.arxiv_id else "")
            + (f" [doi:{s.doi}]" if s.doi else "")
            + (f" [{s.url}]" if s.url else "")
            for s in row.sources
        )
        lines.append(
            f"### {row.id} (tier {row.tier}{', DISCLOSED GAP' if row.disclosed_gap else ''})\n"
            f"Principle: {row.principle}\n"
            f"Sources: {source_bits or 'none (disclosed gap)'}\n"
            f"Check licensed: {row.check}\n"
            + (f"Check notes: {row.check_notes}\n" if row.check_notes else "")
            + f"Failure caught: {row.failure_mode}\n"
            + (f"Precedence: {row.precedence.note}\n" if row.precedence is not None else "")
        )
    return "\n".join(lines)


class PromptAssembler:
    """Renders jinja2 templates into AssembledPrompt objects."""

    def __init__(self, search_paths: list[Path], template_dir: str) -> None:
        directories = [str(p / template_dir) for p in search_paths if (p / template_dir).is_dir()]
        packaged = Path(str(resources.files("creative_agent").joinpath("data", "prompts")))
        if (packaged / template_dir).is_dir():
            directories.append(str(packaged / template_dir))
        if (packaged / "default").is_dir():
            directories.append(str(packaged / "default"))
        if not directories:
            raise ConfigError(f"no prompt template directory found for {template_dir!r}")
        self._env = Environment(
            loader=FileSystemLoader(directories),
            undefined=StrictUndefined,
            autoescape=False,  # prompts are plain text, not HTML
            keep_trailing_newline=True,
        )

    def assemble(
        self,
        kind: CallKind,
        *,
        ref: str = "",
        allowed_tools: list[str],
        fetch_domain_allowlist: list[str],
        context: dict[str, Any],
    ) -> AssembledPrompt:
        model_type = output_model_for(kind)
        schema = model_type.model_json_schema()
        full_context = {
            **context,
            "output_schema": schema,
            "call_kind": kind.value,
            "ref": ref,
        }
        try:
            system = self._env.get_template("system.md.j2").render(**full_context)
            user = self._env.get_template(f"{kind.value}.md.j2").render(**full_context)
        except TemplateNotFound as exc:
            raise ConfigError(f"prompt template missing: {exc}") from exc
        return AssembledPrompt(
            kind=kind,
            ref=ref,
            system=system,
            user=user,
            output_schema=schema,
            allowed_tools=allowed_tools,
            fetch_domain_allowlist=fetch_domain_allowlist,
        )
