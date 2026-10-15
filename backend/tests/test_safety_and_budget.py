import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.budget import RunBudget
from app.config import Settings
from app.evidence import make_passage, make_source
from app.fetcher import UnsafeURL, fetch_public, validate_public_url
from app.graph import sanitize_report
from app.models import ClaimAssessment, Report, ReportFinding
from app.providers import ProviderFailure, RoleAdapter


def test_shared_model_budget_is_atomic_across_threads():
    budget = RunBudget("quick")
    with ThreadPoolExecutor(max_workers=16) as pool:
        reservations = list(pool.map(lambda _: budget.reserve("models"), range(80)))
    assert sum(reservations) == 19
    assert budget.counts["models"] == 19
    assert budget.ended_by_limit == "models"
    assert all(budget.reserve("models", critical=True) for _ in range(5))
    assert not budget.reserve("models", critical=True)
    assert budget.counts["models"] == 24


def test_model_retry_reserves_each_transient_attempt():
    class TemporaryFailure(Exception):
        status_code = 503

    class Runnable:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, value, config):
            self.calls += 1
            if self.calls == 1:
                raise TemporaryFailure()
            return "ok"

    async def scenario():
        budget = RunBudget("quick")
        adapter = RoleAdapter(Settings(app_mode="live", openai_api_key="test"), budget)
        runnable = Runnable()
        result = await adapter._invoke(runnable, "prompt", "Planner")
        return result, runnable.calls, budget.counts["models"]

    result, calls, reservations = asyncio.run(scenario())
    assert (result, calls, reservations) == ("ok", 2, 2)


def test_model_authentication_error_is_not_retried():
    class AuthenticationFailure(Exception):
        status_code = 401

    class Runnable:
        calls = 0

        async def ainvoke(self, value, config):
            self.calls += 1
            raise AuthenticationFailure()

    async def scenario():
        budget = RunBudget("quick")
        adapter = RoleAdapter(Settings(app_mode="live", openai_api_key="test"), budget)
        runnable = Runnable()
        with pytest.raises(ProviderFailure):
            await adapter._invoke(runnable, "prompt", "Planner")
        return runnable.calls, budget.counts["models"]

    calls, reservations = asyncio.run(scenario())
    assert (calls, reservations) == (1, 1)


def test_report_sanitizer_rejects_fabricated_claim_and_source_ids():
    assessment = ClaimAssessment(
        claim_id="claim_real",
        text="Exact assessed wording",
        status="supported",
        supporting_evidence_ids=["ev1"],
        explanation="Direct support",
    )
    report = Report(
        overview="Overview",
        findings=[
            ReportFinding(
                heading="Finding",
                text="Writer invented a stronger claim",
                claim_ids=["claim_fake"],
                kind="evidence",
            )
        ],
        source_ids=["src_fake"],
    )
    cleaned = sanitize_report(report, [assessment], [{"id": "src_real"}])
    assert cleaned.findings == []
    assert cleaned.source_ids == []


def test_report_sanitizer_replaces_factual_wording_with_assessed_claim():
    assessment = ClaimAssessment(
        claim_id="claim_real",
        text="Exact assessed wording",
        status="supported",
        supporting_evidence_ids=["ev1"],
        explanation="Direct support",
    )
    report = Report(
        overview="Overview",
        findings=[
            ReportFinding(
                heading="Finding",
                text="Overstated wording",
                claim_ids=["claim_real"],
                kind="evidence",
            )
        ],
        source_ids=["src_real"],
    )
    cleaned = sanitize_report(report, [assessment], [{"id": "src_real"}])
    assert cleaned.findings[0].text == "Exact assessed wording"
    assert cleaned.source_ids == ["src_real"]


def test_failed_extraction_cannot_create_supporting_passage():
    source = make_source("https://example.org/missing", "Unavailable", "", "text/html")
    assert source.extraction_status == "failed"
    assert make_passage(source, "invented excerpt") is None


def test_url_validator_rejects_credentials_non_http_and_local_hosts():
    for url in (
        "file:///etc/passwd",
        "http://user:pass@example.org/",
        "http://localhost/admin",
        "http://service.local/",
    ):
        with pytest.raises(UnsafeURL):
            asyncio.run(validate_public_url(url))


def test_redirect_target_is_revalidated_before_request(monkeypatch):
    import app.fetcher as fetcher

    calls = []

    class FakeResponse:
        status_code = 302
        headers = {"location": "http://localhost/private"}
        url = "https://8.8.8.8/start"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, headers):
            calls.append(url)
            return FakeResponse()

    monkeypatch.setattr(fetcher.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(fetcher, "_public_addresses", lambda host: ["8.8.8.8"])
    with pytest.raises(UnsafeURL):
        asyncio.run(
            fetch_public(
                "https://public.example/start", max_bytes=1000, max_pages=1, max_chars=1000
            )
        )
    assert calls == ["https://public.example/start"]
