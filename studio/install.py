"""Link the skills and the command into each agent already on this machine.

Only ever adds symlinks, and only removes symlinks that point into this repo. An agent whose folder doesn't exist is left
alone, so this never creates configuration for software that isn't installed.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
COMMAND = "remix-studio"

AGENT_SKILL_DIRS = {
    "claude": ".claude/skills",
    "codex": ".codex/skills",
    "shared (.agents)": ".agents/skills",
    "cursor": ".cursor/skills",
    "gemini": ".gemini/skills",
    "opencode": ".config/opencode/skills",
    "factory": ".factory/skills",
    "hermes": ".hermes/skills/media",
    "kiro": ".kiro/skills",
    "continue": ".continue/skills",
}


@dataclass(frozen=True)
class Step:
    link: Path
    target: Path
    state: str  # "add" | "ok" | "conflict"
    agent: str = ""


def skill_dirs() -> list[Path]:
    return sorted(p for p in SKILLS.iterdir() if (p / "SKILL.md").is_file())


def _state(link: Path, target: Path) -> str:
    if link.is_symlink():
        return "ok" if link.resolve() == target.resolve() else "conflict"
    return "conflict" if link.exists() else "add"


def plan(home: Path, extra_targets: list[Path] | None = None, command_target: Path | None = None) -> list[Step]:
    steps = []
    roots = [(agent, home / rel) for agent, rel in AGENT_SKILL_DIRS.items()]
    roots += [(str(t), t) for t in extra_targets or []]
    for agent, folder in roots:
        if not folder.is_dir():
            continue
        for skill in skill_dirs():
            link = folder / skill.name
            steps.append(Step(link, skill, _state(link, skill), agent))
    if command_target:
        link = home / ".local" / "bin" / COMMAND
        steps.append(Step(link, command_target, _state(link, command_target), "command"))
    return steps


def apply(steps: list[Step]) -> None:
    for s in steps:
        if s.state == "add":
            s.link.parent.mkdir(parents=True, exist_ok=True)
            s.link.symlink_to(s.target)


def remove(home: Path, extra_targets: list[Path] | None = None) -> list[Path]:
    removed = []
    folders = [home / rel for rel in AGENT_SKILL_DIRS.values()] + list(extra_targets or [])
    candidates = [f / s.name for f in folders for s in skill_dirs()] + [home / ".local" / "bin" / COMMAND]
    for link in candidates:
        if link.is_symlink() and _inside_repo(link):
            link.unlink()
            removed.append(link)
    return removed


def _inside_repo(link: Path) -> bool:
    try:
        link.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def command_path() -> Path:
    return Path(sys.prefix) / "bin" / COMMAND
