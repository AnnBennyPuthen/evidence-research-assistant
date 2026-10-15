# Evidence-Based AI Research Assistant

A local-first research desk for technical questions. It plans a bounded investigation, researches independent subquestions, inspects public webpages and text PDFs, assesses claims against exact passages, and returns a structured report with traceable citations. Its statuses describe retrieved evidence; they do not establish absolute truth.

## What Is Included

- Four LLM-backed roles in one typed LangGraph: Planner, Researcher, Evidence Verifier, and Report Writer.
- Parallel task-local research with a shared atomic budget, bounded tool loop, follow-up limits, and evidence/source deduplication.
- Exact excerpt offsets, content hashes, source metadata, reference validation, local SQLite history, cursor-based events, cancellation, interruption marking, and JSON/Markdown exports.
- Deterministic `APP_MODE=demo` with original synthetic examples for supported, conflicting, and insufficient evidence. Demo makes no external model, search, or fetch calls.
- `APP_MODE=live` with OpenAI structured output/tool calling, Tavily Search, guarded HTTP fetching, HTML extraction, and text PDF parsing.
- React, Vite, TypeScript and CSS research workspace; pytest and Vitest tests; CI; evaluation dataset and baseline.

## Run Locally

Use two terminals from the project root. Python 3.11+ and Node.js 20+ are required. This version was tested with Python 3.13.5, Node.js 20.19.4, and npm 11.6.4. Exact backend packages are in `backend/requirements-lock.txt`; `frontend/package-lock.json` records the npm dependency graph. Key tested versions: FastAPI 0.143.1, Pydantic 2.14.1, pydantic-settings 2.16.0, LangGraph 1.2.15, LangChain Core 1.6.10, langchain-openai 1.7.1, httpx 0.28.1, Beautiful Soup 4.15.0, and pypdf 6.20.1.

**PowerShell, terminal 1:**

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
# APP_MODE=demo is already the default; no provider keys are needed.
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**PowerShell, terminal 2:**

```powershell
cd frontend
npm ci
npm run dev
```

If port 8000 is already occupied, start the API with `--port 8001` and set `VITE_API_PROXY_TARGET=http://127.0.0.1:8001` in `frontend/.env.local` or the frontend shell before `npm run dev`. This is a non-secret URL, not a provider key.

Open http://127.0.0.1:5173. The API is at http://127.0.0.1:8000; interactive API documentation is at http://127.0.0.1:8000/docs.

**Bash, terminal 1:**

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**Bash, terminal 2:**

```bash
cd frontend
npm ci
npm run dev
```

If using API port 8001, set `VITE_API_PROXY_TARGET=http://127.0.0.1:8001` in `frontend/.env.local` before starting Vite.

## Live Providers

Live mode never silently falls back to demo. Set these only in `backend/.env`:

```dotenv
APP_MODE=live
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-6-luna
TAVILY_API_KEY=your-key
```

The model catalog checked for this implementation listed `gpt-6-luna` as the cost-sensitive flagship and documented tools. Confirm the current model catalog and pricing before live work; pricing and availability can change. You can set another documented tool-calling and structured-output-compatible model with `OPENAI_MODEL`. Live research makes billable provider requests. Automated tests and normal demo use do not.

## Run Checks

```powershell
cd backend
python -m pytest -q
```

```powershell
cd frontend
npm run typecheck
npm run test
npm run build
```

**Bash checks:**

```bash
cd backend
python -m pytest -q
python -m ruff check app tests
```

```bash
cd frontend
npm run typecheck
npm run test
npm run build
```

The baseline comparison is opt-in and paid. From `backend/`, use `python -m app.evaluation --run-live --question-limit 1 --depth quick` only when you authorize live OpenAI and Tavily calls. Results are written to ignored `backend/evaluation-results/measurements.json` and `.csv`.

## One Request, End To End

1. The browser validates the question form and posts the question, depth, and preferred domains to `POST /api/research`. The local runner records a queued SQLite row and returns a run ID.
2. LangGraph's Planner creates a bounded Pydantic-validated plan. Conditional `Send` tasks run Researcher instances with task-local results; an asyncio semaphore holds concurrency at two.
3. Each researcher chooses bounded Tavily searches and inspected URL fetches. In live mode, public HTTP(S) destinations and each redirect are checked, bytes/pages/text are capped, and text is extracted. PDFs are parsed off the event loop; there is no OCR.
4. Application code assigns source, passage, and claim IDs. It checks every excerpt against the normalized document text and its character offsets, merges duplicate sources/passages, then the Evidence Verifier assesses each atomic candidate claim and links support and contradiction separately.
5. The Report Writer returns structured sections. Deterministic checks remove unknown references and replace factual finding wording with the assessed claim wording. Polling displays workflow events, not private model reasoning. SQLite keeps the report, source evidence, events, and usage; the browser can inspect a claim or export JSON/Markdown.

## API At A Glance

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
$run = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/research -ContentType 'application/json' -Body '{"question":"How does graph orchestration compare with a linear workflow?","depth":"quick","preferred_domains":[]}'
Invoke-RestMethod "http://127.0.0.1:8000/api/research/$($run.run_id)"
Invoke-RestMethod "http://127.0.0.1:8000/api/research/$($run.run_id)/events?cursor=0"
```

The API also provides `GET /api/config`, `GET /api/research`, `POST /api/research/{id}/cancel`, and `GET /api/research/{id}/export?format=markdown|json`.

## Portfolio And Demo

**Portfolio description:** “Built a local-first, evidence-traceable technical research assistant with FastAPI, Pydantic, LangGraph, OpenAI, Tavily Search, SQLite, and React. The workflow separates planning, bounded research, evidence verification, and report writing; it validates citations against inspected passages and provides a deterministic no-network demo mode.”

AI-assisted development disclosure: this first version was developed with AI assistance. Review, understand, and adapt it before publishing; do not present generated work as employer work or claim measured accuracy, time savings, or live validation that you have not independently established. This project intentionally contains no employer code, internal documents, branding, or private datasets.

To create screenshots, run demo mode, submit the default sample question, wait for the report, select a claim, and capture both the report and evidence inspector at desktop and mobile widths. Screenshots are intentionally not fabricated or included. Demo script: show the demo label; run one question; open supported and conflicting claims; inspect an exact excerpt and synthetic source label; show an insufficient-evidence gap; export Markdown and JSON; explain that the workflow has no live source verification in demo mode.

Read first: [architecture](docs/architecture.md), [learning guide](docs/learning-guide.md), [limitations](docs/limitations.md), [evaluation](docs/evaluation.md), and [GitHub setup](docs/github-setup.md).

## Storage And Deployment Scope

SQLite is a local convenience with no external database setup; PostgreSQL is a reasonable later replacement if multiple writers/users or production operations are needed. This is a local single-user portfolio app, not a public service. A live deployment needs authentication, rate limits, durable worker design, stronger network egress controls, monitoring, and reviewed data-retention policies. See [limitations](docs/limitations.md).

There is no license file. Choose a license yourself before publishing; adding one grants others specified rights and should be an intentional decision. GitHub steps are in [docs/github-setup.md](docs/github-setup.md). No Git repository, remote, commit, push, publication, or deployment was created by this build.

## Official Documentation Used

- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [LangChain models](https://docs.langchain.com/oss/python/langchain/models)
- [Tavily Search endpoint](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [OpenAI model catalog](https://developers.openai.com/api/docs/models)
- [Pydantic Settings](https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/)
- [FastAPI tutorial](https://fastapi.tiangolo.com/tutorial/)
