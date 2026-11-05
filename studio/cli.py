"""remix-studio: measure, plan, generate, render and score video edits. Run `remix-studio doctor` first."""
from __future__ import annotations

import argparse
import importlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import install, services

PIPELINES = {"analyze": "studio.analyze", "edl": "studio.edl", "score": "studio.score", "ledger": "studio.ledger",
             "gen": "studio.gen", "swap": "studio.swap"}


def where() -> dict:
    root = install.ROOT
    assets = root / "skills" / "remix" / "assets"
    return {"root": str(root), "assets": str(assets), "fonts": str(assets / "fonts"), "music": str(assets / "music"), "sfx": str(assets / "sfx"),
            "spec": str(root / "docs" / "SPEC.md"), "services": str(services.services_path()), "env": str(services.env_path()),
            "services_example": str(root / "examples" / "services.example.json")}


def _ffmpeg_has(filter_name: str) -> bool:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return any(f" {filter_name} " in line for line in r.stdout.splitlines())


def doctor(home: Path) -> tuple[list[str], list[str]]:
    """Returns (report lines, problems that stop an edit). Prints key names only, never their values."""
    lines, problems = [], []

    def check(ok: bool, label: str, fix: str = "", required: bool = True) -> None:
        lines.append(f"{'ok  ' if ok else ('FAIL' if required else 'note')}  {label}{'' if ok or not fix else ': ' + fix}")
        if required and not ok:
            problems.append(label)

    check(sys.version_info >= (3, 11), f"python {platform.python_version()}", "needs 3.11 or newer")
    for tool in ("ffmpeg", "ffprobe"):
        check(shutil.which(tool) is not None, tool, "install ffmpeg, for example `brew install ffmpeg`")
    if shutil.which("ffmpeg"):
        check(_ffmpeg_has("drawtext"), "ffmpeg drawtext filter", "this ffmpeg build cannot draw text")
    check(platform.system() == "Darwin" and shutil.which("clang") is not None, "subject mattes for type behind the subject",
          "needs macOS 14 or later and clang; everything else still works", required=False)

    linked = [s for s in install.plan(home) if s.state == "ok"]
    agents = sorted({s.agent for s in linked})
    check(bool(agents), f"skills linked for: {', '.join(agents) or 'none'}", "run ./install.sh", required=False)
    conflicts = [s for s in install.plan(home) if s.state == "conflict"]
    for s in conflicts:
        check(False, f"{s.link} already exists and isn't ours", "left alone", required=False)
    check(shutil.which(install.COMMAND) is not None, f"`{install.COMMAND}` is on PATH", "add ~/.local/bin to PATH", required=False)

    routes = []
    try:
        for name in sorted(services.load_all()):
            svc = services.get(name)
            missing = svc.missing_env()
            check(not missing, f"service http:{name}", f"set {', '.join(missing)} in {services.env_path()}", required=False)
            if not missing:
                routes.append(f"http:{name}")
    except services.ServiceConfigError as e:
        check(False, "services.json", str(e))
    if shutil.which("treg"):
        routes.append("treg")
    check(bool(routes), f"generation routes: {', '.join(routes) or 'none'}",
          f"copy {where()['services_example']} to {services.services_path()} and fill it in. Analysis and rendering work without this", required=False)
    return lines, problems


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in PIPELINES:
        return importlib.import_module(PIPELINES[argv[0]]).main(argv[1:]) or 0

    ap = argparse.ArgumentParser(prog=install.COMMAND, description=__doc__,
                                 epilog="Pipelines, each with its own --help: " + ", ".join(PIPELINES))
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("where", help="print where assets, docs and configuration live, as JSON")
    sub.add_parser("doctor", help="check this machine and list what's ready")
    p = sub.add_parser("install-skills", help="link the skills into each agent found, and the command into ~/.local/bin")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--target", action="append", default=[], metavar="DIR", help="another skills folder to link into")
    p = sub.add_parser("uninstall-skills", help="remove only the links that point into this repo")
    p.add_argument("--target", action="append", default=[], metavar="DIR")
    for name in PIPELINES:
        sub.add_parser(name, help=f"see `{install.COMMAND} {name} --help`", add_help=False)
    a = ap.parse_args(argv)
    home = Path.home()

    if a.cmd == "where":
        print(json.dumps(where(), indent=1))
        return 0
    if a.cmd == "doctor":
        lines, problems = doctor(home)
        print("\n".join(lines))
        return 1 if problems else 0
    if a.cmd == "install-skills":
        steps = install.plan(home, [Path(t).expanduser() for t in a.target], install.command_path())
        for s in steps:
            print(f"{s.state:8} {s.agent:18} {s.link} -> {s.target}")
        if not a.dry_run:
            install.apply(steps)
        return 1 if any(s.state == "conflict" for s in steps) else 0
    if a.cmd == "uninstall-skills":
        for link in install.remove(home, [Path(t).expanduser() for t in a.target]):
            print(f"removed {link}")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
