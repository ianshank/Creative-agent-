#!/usr/bin/env python3
"""M0 keystone spike: validate the two SDK assumptions the harness is built on.

Run manually with ANTHROPIC_API_KEY set (also exercised weekly by live.yml):

    uv run python scripts/sdk_spike.py

Checks:
1. `ClaudeAgentOptions.output_format={"type": "json_schema", ...}` yields schema-valid
   structured output on `ResultMessage` (DEC-F5).
2. The message stream exposes `ToolUseBlock` (name/input on AssistantMessage.content) and
   `ToolResultBlock` (tool_use_id/is_error on UserMessage.content) so fetched-URL evidence
   can be extracted (DEC-F9).

Exits non-zero if either assumption fails, printing the observed surface.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys


async def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — spike skipped (not a pass).")
        return 2

    from claude_agent_sdk import ClaudeAgentOptions, query

    schema = {
        "type": "object",
        "properties": {
            "fetched_title": {"type": "string"},
            "arxiv_id": {"type": "string"},
        },
        "required": ["fetched_title", "arxiv_id"],
    }
    options = ClaudeAgentOptions(
        output_format={"type": "json_schema", "schema": schema},
        allowed_tools=["WebFetch"],
        permission_mode="dontAsk",
        max_turns=4,
    )
    tool_uses: list[dict[str, object]] = []
    tool_results: list[dict[str, object]] = []
    structured: object = None

    async for message in query(
        prompt=(
            "Fetch https://arxiv.org/abs/2208.11173 and report its title and arXiv id "
            "in the structured output."
        ),
        options=options,
    ):
        kind = type(message).__name__
        content = getattr(message, "content", None)
        if isinstance(content, list):
            for block in content:
                block_kind = type(block).__name__
                if block_kind == "ToolUseBlock":
                    tool_uses.append({"name": block.name, "input": block.input})
                elif block_kind == "ToolResultBlock":
                    tool_results.append(
                        {"tool_use_id": block.tool_use_id, "is_error": block.is_error}
                    )
        if kind == "ResultMessage":
            structured = getattr(message, "structured_output", None) or getattr(
                message, "result", None
            )
            print(f"ResultMessage subtype={getattr(message, 'subtype', None)}")

    print("tool_uses:", json.dumps(tool_uses, default=str, indent=2))
    print("tool_results:", json.dumps(tool_results, default=str, indent=2))
    print("structured:", structured)

    ok = bool(tool_uses) and bool(tool_results) and structured is not None
    print("SPIKE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
