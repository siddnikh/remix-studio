import json
import re
from pathlib import Path

import pytest

from studio import cli, install

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".claude/skills").mkdir(parents=True)
    (tmp_path / ".codex/skills").mkdir(parents=True)
    return tmp_path


def test_links_every_skill_only_into_agents_that_exist(home):
    steps = install.plan(home)
    assert all(s.state == "add" for s in steps)
    install.apply(steps)
    for skill in ("remix", "subject-swap"):
        assert (home / ".claude/skills" / skill / "SKILL.md").is_file()
        assert (home / ".codex/skills" / skill / "SKILL.md").is_file()
    assert not (home / ".cursor").exists()


def test_install_twice_changes_nothing(home):
    install.apply(install.plan(home))
    second = install.plan(home)
    assert second and all(s.state == "ok" for s in second)


def test_never_overwrites_something_that_is_not_ours(home):
    mine = home / ".claude/skills/remix"
    mine.mkdir()
    (mine / "SKILL.md").write_text("mine")
    other = home / ".codex/skills/remix"
    other.symlink_to(home)
    steps = install.plan(home)
    install.apply(steps)
    assert {s.link for s in steps if s.state == "conflict"} == {mine, other}
    assert (mine / "SKILL.md").read_text() == "mine" and other.resolve() == home.resolve()


def test_command_link_is_planned_with_the_skills(home, tmp_path):
    target = tmp_path / "bin-target"
    target.write_text("x")
    install.apply(install.plan(home, command_target=target))
    assert (home / ".local/bin/remix-studio").resolve() == target.resolve()


def test_extra_target_folder(home, tmp_path):
    extra = tmp_path / "custom-agent"
    extra.mkdir()
    install.apply(install.plan(home, [extra]))
    assert (extra / "remix" / "SKILL.md").is_file()


def test_uninstall_removes_only_our_links(home):
    install.apply(install.plan(home))
    foreign = home / ".claude/skills/other"
    foreign.symlink_to(home)
    removed = install.remove(home)
    assert home / ".claude/skills/remix" in removed
    assert not (home / ".claude/skills/remix").exists() and foreign.is_symlink()


def test_uninstall_leaves_a_folder_with_our_name_alone(home):
    mine = home / ".claude/skills/remix"
    mine.mkdir()
    assert install.remove(home) == [] and mine.is_dir()


def test_where_points_at_real_files(capsys):
    assert cli.main(["where"]) == 0
    w = json.loads(capsys.readouterr().out)
    for key in ("root", "assets", "fonts", "music", "sfx", "spec"):
        assert Path(w[key]).exists(), key


def test_every_pipeline_is_reachable_from_the_command(capsys):
    for name in cli.PIPELINES:
        with pytest.raises(SystemExit) as e:
            cli.main([name, "--help"])
        assert e.value.code == 0
    capsys.readouterr()


def test_doctor_reports_key_names_and_never_values(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIX_STUDIO_HOME", str(tmp_path))
    monkeypatch.setenv("PRESENT_KEY", "sk-secret-value")
    monkeypatch.delenv("ABSENT_KEY", raising=False)
    (tmp_path / "services.json").write_text(json.dumps({
        "a": {"submit": {"url": "http://x", "headers": {"A": "${PRESENT_KEY}"}}},
        "b": {"submit": {"url": "http://x", "headers": {"A": "${ABSENT_KEY}"}}}}))
    lines, _ = cli.doctor(tmp_path)
    text = "\n".join(lines)
    assert "sk-secret-value" not in text and "ABSENT_KEY" in text and "http:a" in text


def skill_files():
    return sorted((ROOT / "skills").glob("*/SKILL.md"))


def test_each_skill_has_the_frontmatter_every_agent_needs():
    assert {p.parent.name for p in skill_files()} == {"remix", "subject-swap"}
    for path in skill_files():
        head = path.read_text().split("---")[1]
        assert re.search(rf"^name: {path.parent.name}$", head, re.M), path
        assert "description:" in head


def test_skills_work_outside_the_repo():
    for path in [*skill_files(), *(ROOT / "skills").glob("*/references/*.md")]:
        text = path.read_text()
        assert ".venv/" not in text and "python -m studio" not in text, path
        for link in re.findall(r"\]\(([^)#]+)\)", text):
            if not link.startswith("http"):
                assert (path.parent / link).exists(), f"{path}: {link}"


def test_nothing_that_runs_depends_on_higgsfield():
    files = [*(ROOT / "studio").glob("*.py"), *skill_files()]
    for path in files:
        assert "higgsfield" not in path.read_text().lower(), path
