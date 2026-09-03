"""Validation for Claude Code assets (agents, skills, hooks, settings).

The `.claude/` directory is executable configuration: a subagent with a malformed
frontmatter block, a skill whose description never triggers, a hook that is not
executable, or a settings file granting a tool the harness does not use are all defects
that no Python test would otherwise catch. They fail silently at the worst moment —
mid-review, in someone else's session.

This module is the schema for those assets so the test suite can assert them
deterministically, and so a future `creative-agent assets validate` command has one
implementation to call. Nothing here imports the agents package: assets are data.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from creative_agent.harness.logging import get_logger, log_event

_LOGGER = get_logger(__name__)

_FRONT_MATTER = re.compile(r"\A---\r?\n(?P<yaml>.*?)\r?\n---\r?\n", re.DOTALL)
# A name that appears in a slash command or an agent reference: lowercase, hyphenated.
_ASSET_NAME = re.compile(r"\A[a-z][a-z0-9-]*\Z")

# Frontmatter keys each asset kind may declare. Unknown keys are reported rather than
# ignored: a typo'd key is silently inert, which is exactly the failure mode to catch.
AGENT_KEYS = frozenset({"name", "description", "tools", "model", "color"})
SKILL_KEYS = frozenset({"name", "description", "allowed-tools", "license", "version"})
REQUIRED_KEYS = frozenset({"name", "description"})

# Descriptions are the trigger surface: too short and the asset never fires.
MIN_DESCRIPTION_CHARS = 40


@dataclass(frozen=True)
class AssetKind:
    """One directory of assets a `.claude` tree must contain, and how to find its files."""

    name: str
    directory: str
    pattern: str

    def files(self, claude_dir: Path) -> list[Path]:
        """Every file of this kind, or an empty list if the directory is absent."""
        source = claude_dir / self.directory
        return sorted(source.glob(self.pattern)) if source.is_dir() else []


# The expected inventory, as data rather than literals inside `collect`: each kind must
# actually yield something. Every walk below is guarded by `is_dir()`, so a renamed,
# moved or gutted directory — `agents/` to `agent/` — produced an empty inventory, no
# defects, and a clean "all assets valid". A validator that passes on nothing is worse
# than none, because it is trusted. This mirrors the oracle path, which already refuses to
# call a run successful when it loaded no oracle files.
AGENT_ASSETS = AssetKind("agent", "agents", "*.md")
SKILL_ASSETS = AssetKind("skill", "skills", "*/SKILL.md")
HOOK_ASSETS = AssetKind("hook", "hooks", "*.sh")
EXPECTED_ASSET_KINDS: tuple[AssetKind, ...] = (AGENT_ASSETS, SKILL_ASSETS, HOOK_ASSETS)

# settings.json is a single file rather than a directory of them, so it is named on its
# own — but it is required for the same reason: the hooks it wires up do not run without
# it, and its absence is silent.
SETTINGS_FILENAME = "settings.json"


@dataclass
class AssetDefect:
    """One problem found in an asset, addressed to whoever must fix it."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class AssetInventory:
    """What was found on disk, so tests can assert presence as well as validity."""

    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    skills: dict[str, dict[str, Any]] = field(default_factory=dict)
    hooks: list[Path] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)


def parse_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    """Split a markdown asset into (frontmatter, body). Raises ValueError if malformed."""
    text = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(text)
    if not match:
        raise ValueError("missing YAML front matter delimited by --- lines")
    try:
        loaded = yaml.safe_load(match.group("yaml"))
    except yaml.YAMLError as exc:
        raise ValueError(f"unparseable front matter: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("front matter must be a mapping")
    return loaded, text[match.end() :]


def _check_common(path: Path, meta: dict[str, Any], allowed: frozenset[str]) -> list[AssetDefect]:
    defects: list[AssetDefect] = []
    rel = path.name
    missing = REQUIRED_KEYS - set(meta)
    if missing:
        defects.append(AssetDefect(rel, f"missing required frontmatter keys {sorted(missing)}"))
    unknown = set(meta) - allowed
    if unknown:
        defects.append(
            AssetDefect(rel, f"unknown frontmatter keys {sorted(unknown)} (a typo is inert)")
        )
    name = meta.get("name")
    if isinstance(name, str) and not _ASSET_NAME.match(name):
        defects.append(
            AssetDefect(rel, f"name {name!r} must be lowercase and hyphenated to be invocable")
        )
    description = meta.get("description")
    if isinstance(description, str) and len(description.strip()) < MIN_DESCRIPTION_CHARS:
        defects.append(
            AssetDefect(
                rel,
                f"description is {len(description.strip())} chars; under "
                f"{MIN_DESCRIPTION_CHARS} it is unlikely to trigger reliably",
            )
        )
    return defects


def validate_agent(path: Path, known_tools: frozenset[str] | None = None) -> list[AssetDefect]:
    """Validate one subagent definition."""
    try:
        meta, body = parse_front_matter(path)
    except ValueError as exc:
        return [AssetDefect(path.name, str(exc))]
    defects = _check_common(path, meta, AGENT_KEYS)
    if path.stem != meta.get("name"):
        defects.append(
            AssetDefect(
                path.name,
                f"filename must match name {meta.get('name')!r} to be addressable",
            )
        )
    tools = meta.get("tools")
    if tools is not None:
        declared = [t.strip() for t in tools.split(",")] if isinstance(tools, str) else list(tools)
        if not declared:
            defects.append(AssetDefect(path.name, "tools declared but empty"))
        if known_tools is not None:
            unknown = sorted(set(declared) - set(known_tools))
            if unknown:
                defects.append(AssetDefect(path.name, f"unknown tools {unknown}"))
    if not body.strip():
        defects.append(AssetDefect(path.name, "empty body: the agent has no instructions"))
    return defects


def validate_skill(path: Path) -> list[AssetDefect]:
    """Validate one skill definition (path is the SKILL.md file)."""
    try:
        meta, body = parse_front_matter(path)
    except ValueError as exc:
        return [AssetDefect(str(path.parent.name), str(exc))]
    rel = f"{path.parent.name}/SKILL.md"
    defects = [AssetDefect(rel, d.message) for d in _check_common(path, meta, SKILL_KEYS)]
    if path.parent.name != meta.get("name"):
        defects.append(
            AssetDefect(rel, f"directory must match name {meta.get('name')!r} to be invocable")
        )
    if not body.strip():
        defects.append(AssetDefect(rel, "empty body: the skill has no procedure"))
    return defects


# os.name is 'posix' on Linux and macOS, 'nt' on Windows.
_EXECUTE_BIT_IS_MEANINGFUL = os.name == "posix"


def validate_hook(path: Path) -> list[AssetDefect]:
    """Validate one hook script: executable, shebang, and fail-safe shell options."""
    defects: list[AssetDefect] = []
    # The execute bit is a POSIX concept. Windows never sets it, so checking it there
    # would flag every hook in a clean checkout and make `assets validate` exit 1 for a
    # reason that has nothing to do with the assets — a second, independent Windows break
    # alongside the fcntl import (docs/roadmap.md 4.2).
    if _EXECUTE_BIT_IS_MEANINGFUL and not path.stat().st_mode & stat.S_IXUSR:
        defects.append(AssetDefect(path.name, "not executable: the hook will not run (chmod +x)"))
    text = path.read_text(encoding="utf-8")
    if not text.startswith("#!"):
        defects.append(AssetDefect(path.name, "missing shebang"))
    if "set -" not in text:
        defects.append(AssetDefect(path.name, "no `set -e`/`set -u`: failures would pass silently"))
    return defects


def validate_settings(path: Path, hook_dir: Path) -> list[AssetDefect]:
    """Validate settings.json: parseable, and every hook command resolves to a script."""
    defects: list[AssetDefect] = []
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [AssetDefect(path.name, f"invalid JSON: {exc}")]
    for event, entries in settings.get("hooks", {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                command = str(hook.get("command", ""))
                # Commands are written with $CLAUDE_PROJECT_DIR; resolve to check them.
                referenced = command.replace("$CLAUDE_PROJECT_DIR/.claude/hooks/", "")
                if referenced.endswith(".sh") and not (hook_dir / referenced).is_file():
                    defects.append(
                        AssetDefect(
                            path.name,
                            f"{event} hook references missing script {referenced}",
                        )
                    )
    return defects


def _empty_inventory_defects(
    claude_dir: Path, found: dict[str, int], has_settings: bool
) -> list[AssetDefect]:
    """One defect per asset kind that yielded nothing at all.

    Not "this asset is malformed" but "there is no asset here to be malformed" — the
    condition under which every other check in this module silently has nothing to say.
    """
    defects = [
        AssetDefect(
            f"{claude_dir.name}/{kind.directory}",
            f"no {kind.name} files matching {kind.pattern!r}: the directory is missing, "
            "renamed or empty, so validation would otherwise pass on an empty inventory",
        )
        for kind in EXPECTED_ASSET_KINDS
        if not found.get(kind.name)
    ]
    if not has_settings:
        defects.append(
            AssetDefect(
                f"{claude_dir.name}/{SETTINGS_FILENAME}",
                "missing: permissions and hook wiring are unset, and every hook this "
                "directory ships is inert",
            )
        )
    return defects


def collect(
    claude_dir: Path, known_tools: frozenset[str] | None = None
) -> tuple[AssetInventory, list[AssetDefect]]:
    """Load and validate every asset under a `.claude` directory.

    An inventory that came back empty is itself a defect: see `_empty_inventory_defects`.
    """
    inventory = AssetInventory()
    defects: list[AssetDefect] = []
    found: dict[str, int] = {}

    agent_paths = AGENT_ASSETS.files(claude_dir)
    found[AGENT_ASSETS.name] = len(agent_paths)
    for path in agent_paths:
        defects.extend(validate_agent(path, known_tools))
        try:
            meta, _ = parse_front_matter(path)
        except ValueError:
            continue
        inventory.agents[str(meta.get("name", path.stem))] = meta

    skill_paths = SKILL_ASSETS.files(claude_dir)
    found[SKILL_ASSETS.name] = len(skill_paths)
    for path in skill_paths:
        defects.extend(validate_skill(path))
        try:
            meta, _ = parse_front_matter(path)
        except ValueError:
            continue
        inventory.skills[str(meta.get("name", path.parent.name))] = meta

    hook_paths = HOOK_ASSETS.files(claude_dir)
    found[HOOK_ASSETS.name] = len(hook_paths)
    for path in hook_paths:
        inventory.hooks.append(path)
        defects.extend(validate_hook(path))

    settings_path = claude_dir / SETTINGS_FILENAME
    has_settings = settings_path.is_file()
    if has_settings:
        defects.extend(validate_settings(settings_path, claude_dir / HOOK_ASSETS.directory))
        with contextlib.suppress(json.JSONDecodeError):
            inventory.settings = json.loads(settings_path.read_text(encoding="utf-8"))

    missing = _empty_inventory_defects(claude_dir, found, has_settings)
    if missing:
        log_event(
            _LOGGER,
            logging.WARNING,
            "assets.inventory_incomplete",
            claude_dir=str(claude_dir),
            missing_kinds=[defect.path for defect in missing],
            counts=found,
        )
    defects.extend(missing)

    return inventory, defects


def default_claude_dir() -> Path:
    """The repository's `.claude` directory, from an env override or the package layout."""
    override = os.environ.get("CREATIVE_AGENT_CLAUDE_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / ".claude"
