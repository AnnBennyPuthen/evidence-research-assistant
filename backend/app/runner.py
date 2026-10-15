import asyncio
import concurrent.futures
import logging
import time
from uuid import uuid4

import httpx

from .budget import RunBudget
from .config import Settings
from .evidence import canonicalize_url
from .fetcher import fetch_public
from .graph import run_research
from .models import ResearchRequest, ResearchResult
from .providers import ProviderFailure, RoleAdapter
from .repository import Repository

logger = logging.getLogger(__name__)


class RunManager:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository
        self.semaphore = asyncio.Semaphore(settings.max_active_runs)
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancelled: set[str] = set()

    async def start(self, request: ResearchRequest) -> str:
        run_id = f"run_{uuid4().hex[:12]}"
        await asyncio.to_thread(self.repository.create_run, run_id, request.model_dump())
        task = asyncio.create_task(self._execute(run_id, request), name=run_id)
        self.tasks[run_id] = task
        return run_id

    async def _execute(self, run_id: str, request: ResearchRequest):
        started = time.monotonic()
        async with self.semaphore:
            await asyncio.to_thread(
                self.repository.save_result, run_id, "running", {"request": request.model_dump()}
            )
            budget = RunBudget(request.depth)
            roles = RoleAdapter(self.settings, budget)
            cache: dict[str, dict] = {}
            event_loop = asyncio.get_running_loop()
            event_tasks: set[concurrent.futures.Future] = set()
            partial_payload = {
                "run_id": run_id,
                "status": "running",
                "request": request.model_dump(),
                "sources": [],
                "evidence": [],
                "claims": [],
                "report": None,
            }

            async def persist_event(kind: str, message: str, data: dict):
                await asyncio.to_thread(self.repository.add_event, run_id, kind, message, data)

            def add_event(kind: str, message: str, data: dict | None = None):
                if not message or "reasoning" in kind.lower():
                    return
                task = asyncio.run_coroutine_threadsafe(
                    persist_event(kind, message, data or {}), event_loop
                )
                event_tasks.add(task)
                task.add_done_callback(event_tasks.discard)

            async def flush_events():
                pending = list(event_tasks)
                if pending:
                    await asyncio.gather(
                        *(asyncio.wrap_future(task) for task in pending), return_exceptions=True
                    )

            async def persist_partial(snapshot: dict):
                nonlocal partial_payload
                partial_payload = {
                    "run_id": run_id,
                    "status": "running",
                    "request": request.model_dump(),
                    "plan": snapshot.get("plan"),
                    "sources": snapshot.get("sources", []),
                    "evidence": snapshot.get("evidence", []),
                    "claims": snapshot.get("assessments", []),
                    "report": snapshot.get("report"),
                    "usage": {
                        "metrics_scope": "demo" if self.settings.app_mode == "demo" else "live",
                        "search_requests": budget.counts["searches"],
                        "fetch_attempts": budget.counts["fetches"],
                        "ended_by_limit": budget.ended_by_limit,
                    },
                }
                await asyncio.to_thread(
                    self.repository.save_result, run_id, "running", partial_payload
                )

            async def search(query: str, domains: list[str]):
                if self.settings.app_mode == "demo":
                    return []
                if not self.settings.tavily_api_key:
                    raise ProviderFailure("Live mode requires TAVILY_API_KEY in backend/.env")
                payload = {
                    "query": query[:500],
                    "search_depth": "basic",
                    "max_results": 5,
                    "include_answer": False,
                    "include_raw_content": False,
                }
                if domains:
                    payload["include_domains"] = domains
                    payload["include_domains_mode"] = "prefer"
                async with httpx.AsyncClient(timeout=15) as client:
                    for attempt in range(2):
                        if not budget.reserve("searches"):
                            return []
                        response = await client.post(
                            "https://api.tavily.com/search",
                            json=payload,
                            headers={"Authorization": f"Bearer {self.settings.tavily_api_key}"},
                        )
                        if response.status_code in {401, 403}:
                            raise ProviderFailure("Tavily rejected the configured key or plan")
                        if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                            await asyncio.sleep(0.4)
                            continue
                        response.raise_for_status()
                        return response.json().get("results", [])
                return []

            async def fetch(url: str):
                try:
                    normalized = canonicalize_url(url)
                    if normalized in cache:
                        budget.counts["cached_calls"] += 1
                        return cache[normalized]
                    if not budget.reserve("fetches"):
                        return {"status": "failed", "reason": "Fetch attempt budget reached"}
                    result = await fetch_public(
                        url,
                        max_bytes=self.settings.max_fetch_bytes,
                        max_pages=self.settings.max_pdf_pages,
                        max_chars=self.settings.max_document_chars,
                    )
                    if result.get("status") == "ok":
                        cache[normalized] = result
                    return result
                except Exception as exc:
                    return {"status": "failed", "reason": str(exc)[:200]}

            try:
                graph_result, usage = await asyncio.wait_for(
                    run_research(run_id, request, roles, search, fetch, add_event, persist_partial),
                    timeout=budget.limits["seconds"],
                )
                await flush_events()
                usage.elapsed_seconds = round(time.monotonic() - started, 2)
                status = (
                    "cancelled"
                    if run_id in self.cancelled
                    else graph_result.get("status", "completed")
                )
                result = ResearchResult(
                    run_id=run_id,
                    status=status,
                    request=request,
                    plan=graph_result.get("plan"),
                    sources=graph_result.get("sources", []),
                    evidence=graph_result.get("evidence", []),
                    claims=graph_result.get("assessments", []),
                    report=graph_result.get("report"),
                    usage=usage,
                )
                await asyncio.to_thread(
                    self.repository.save_result, run_id, status, result.model_dump(mode="json")
                )
            except asyncio.CancelledError:
                await flush_events()
                partial_payload["status"] = "cancelled"
                await asyncio.to_thread(
                    self.repository.save_result,
                    run_id,
                    "cancelled",
                    partial_payload,
                    "Cancelled by user. In-flight provider requests may still complete or be billed.",
                )
                raise
            except TimeoutError:
                await flush_events()
                partial_payload["status"] = "partial"
                partial_payload["error"] = "Run time limit reached; the saved evidence is partial."
                partial_payload["usage"] = {
                    **partial_payload.get("usage", {}),
                    "ended_by_limit": "seconds",
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                }
                await asyncio.to_thread(
                    self.repository.save_result,
                    run_id,
                    "partial",
                    partial_payload,
                    partial_payload["error"],
                )
            except Exception as exc:
                logger.exception("Research run %s failed", run_id)
                await flush_events()
                message = (
                    str(exc)
                    if isinstance(exc, ProviderFailure)
                    else f"Run failed ({type(exc).__name__}); check backend configuration and logs."
                )
                partial_payload["status"] = "failed"
                partial_payload["error"] = message
                await asyncio.to_thread(
                    self.repository.save_result, run_id, "failed", partial_payload, message
                )
                add_event("error", message)
            finally:
                self.tasks.pop(run_id, None)

    async def cancel(self, run_id: str) -> bool:
        task = self.tasks.get(run_id)
        if not task:
            return False
        row = await asyncio.to_thread(self.repository.get_run, run_id)
        if row and row["status"] == "queued":
            payload = row["payload"]
            await asyncio.to_thread(
                self.repository.save_result,
                run_id,
                "cancelled",
                payload,
                "Cancelled before the local worker started.",
            )
        self.cancelled.add(run_id)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        row = await asyncio.to_thread(self.repository.get_run, run_id)
        if row and row["status"] != "cancelled":
            payload = row["payload"]
            payload["status"] = "cancelled"
            await asyncio.to_thread(
                self.repository.save_result,
                run_id,
                "cancelled",
                payload,
                "Cancelled by user. In-flight provider requests may still complete or be billed.",
            )
        return True
