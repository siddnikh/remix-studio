# remix-studio

Skills and a command-line tool for making video edits. If you're here to use it rather than change it, follow `skills/remix/SKILL.md` or `skills/subject-swap/SKILL.md`.

- Set up with `./install.sh`, which is safe to re-run. Check with `remix-studio doctor`.
- Test with `.venv/bin/python -m pytest -q`. It takes about 3 minutes and spends no money. Nothing in the suite may reach a paid API.
- Generation only happens through `studio/gen.py` and `studio/swap.py`, under the ledger in `studio/ledger.py`. Don't add a paid call that bypasses it.
- A submit without a clear answer keeps its hold, because a job may exist. Keep that rule when adding routes.
- Add a provider through `services.json` (`docs/services.md`) before writing an adapter.
- API keys come from the environment or `~/.config/remix-studio/env` at send time. They must never reach a plan, state file, ledger or log.
- `docs/results/` is a record of what was measured on a date. Don't edit its numbers; add new results alongside them.
- Commit messages describe the code change only.
