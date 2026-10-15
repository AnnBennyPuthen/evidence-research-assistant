# Workspace Guidance

- Keep provider credentials in `backend/.env`; never expose them through `VITE_*`, API responses, fixtures, logs, or committed files.
- `APP_MODE=demo` is deterministic and must make zero external calls. Automated tests use demo or mocked providers only.
- Keep the four roles in `backend/app/graph.py` distinct: Planner, Researcher, Evidence Verifier, and Report Writer. Utility functions are not agents.
- Generate IDs in application code. Validate evidence excerpts against stored text offsets and reject invalid report references.
- Preserve local-only defaults, public HTTP(S) fetch restrictions, bounded budgets, and SQLite persistence.
- Do not claim semantic correctness from structural tests or claim live provider validation without an explicitly authorized live run.
- Do not initialize Git, create remotes, commit, publish, or deploy unless the user explicitly asks.
