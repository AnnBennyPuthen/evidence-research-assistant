import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .config import get_settings
from .models import ResearchRequest
from .repository import Repository
from .runner import RunManager

settings = get_settings()
repository = Repository(settings.database_path)
manager = RunManager(settings, repository)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(repository.interrupt_unfinished)
    yield
    for task in list(manager.tasks.values()):
        task.cancel()
    if manager.tasks:
        await asyncio.gather(*manager.tasks.values(), return_exceptions=True)


app = FastAPI(title="Evidence-Based AI Research Assistant", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok", "mode": settings.app_mode}


@app.get("/api/config")
async def config():
    return {
        "mode": settings.app_mode,
        "providers_available": {
            "openai": bool(settings.openai_api_key),
            "tavily": bool(settings.tavily_api_key),
        },
        "model": settings.openai_model if settings.app_mode == "live" else None,
    }


@app.post("/api/research", status_code=202)
async def create_research(request: ResearchRequest):
    if settings.app_mode == "live" and (not settings.openai_api_key or not settings.tavily_api_key):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "provider_configuration",
                "message": "Live mode requires OPENAI_API_KEY, OPENAI_MODEL, and TAVILY_API_KEY in backend/.env. No demo fallback is used.",
            },
        )
    active = sum(not task.done() for task in manager.tasks.values())
    if active >= settings.max_active_runs:
        raise HTTPException(
            status_code=429, detail={"code": "run_limit", "message": "Maximum active runs reached"}
        )
    run_id = await manager.start(request)
    return {"run_id": run_id, "status": "queued"}


@app.get("/api/research")
async def list_research(limit: int = Query(default=30, ge=1, le=100)):
    return await asyncio.to_thread(repository.list_runs, limit)


@app.get("/api/research/{run_id}")
async def get_research(run_id: str):
    row = await asyncio.to_thread(repository.get_run, run_id)
    if not row:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Research run not found"}
        )
    payload = row["payload"]
    payload["status"] = row["status"]
    if row["error"]:
        payload["error"] = row["error"]
    if not payload.get("run_id"):
        payload = {
            "run_id": run_id,
            "status": row["status"],
            "request": payload.get(
                "request",
                {"question": row["question"], "depth": row["depth"], "preferred_domains": []},
            ),
            "sources": [],
            "evidence": [],
            "claims": [],
            "report": None,
            "usage": {"metrics_scope": "demo" if settings.app_mode == "demo" else "live"},
            "error": row["error"],
        }
    return {**row, "result": payload}


@app.get("/api/research/{run_id}/events")
async def get_events(run_id: str, cursor: int = Query(default=0, ge=0)):
    row = await asyncio.to_thread(repository.get_run, run_id)
    if not row:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Research run not found"}
        )
    events = await asyncio.to_thread(repository.events_after, run_id, cursor)
    return {"events": events, "next_cursor": events[-1]["id"] if events else cursor}


@app.post("/api/research/{run_id}/cancel", status_code=202)
async def cancel_research(run_id: str):
    if not await manager.cancel(run_id):
        raise HTTPException(
            status_code=409, detail={"code": "not_active", "message": "Run is not active"}
        )
    return {"run_id": run_id, "status": "cancelling"}


@app.get("/api/research/{run_id}/export")
async def export_research(
    run_id: str, format: str = Query(default="markdown", pattern="^(markdown|json)$")
):
    row = await asyncio.to_thread(repository.get_run, run_id)
    if not row:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Research run not found"}
        )
    payload = row["payload"]
    if format == "json":
        content = json.dumps(payload, indent=2, ensure_ascii=False)
        media_type, filename = "application/json", f"{run_id}.json"
    else:
        report = payload.get("report") or {}
        lines = [
            "# Evidence-Based Research Report",
            "",
            report.get("demo_label") or "Live research",
            "",
            report.get("overview", "No report available."),
            "",
        ]
        for section, findings in (
            ("Findings", report.get("findings", [])),
            ("Comparison", report.get("comparison", [])),
            ("Conflicts", report.get("conflicts", [])),
        ):
            lines.extend([f"## {section}", ""])
            for finding in findings:
                lines += [
                    f"### {finding['heading']}",
                    finding["text"],
                    f"Claims: {', '.join(finding.get('claim_ids', []))}",
                    "",
                ]
        lines += ["## Gaps", *[f"- {gap}" for gap in report.get("gaps", [])], "", "## Sources"]
        for source in payload.get("sources", []):
            lines.append(f"- {source['title']} ({source['domain']}) - {source['canonical_url']}")
        content = "\n".join(lines)
        media_type, filename = "text/markdown; charset=utf-8", f"{run_id}.md"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
