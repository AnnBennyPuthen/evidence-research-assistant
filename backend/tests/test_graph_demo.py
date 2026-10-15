import asyncio

from app.budget import RunBudget
from app.config import Settings
from app.evidence import make_passage, make_source, validate_passage
from app.graph import run_research
from app.models import EvidencePassage, ResearchRequest, SourceDocument
from app.providers import RoleAdapter


def test_demo_graph_generates_traceable_report_without_network():
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Demo mode must not make external tool calls")

    async def main():
        budget = RunBudget("quick")
        roles = RoleAdapter(Settings(app_mode="demo"), budget)
        events = []
        result, usage = await run_research(
            "run_demo",
            ResearchRequest(
                question="How does graph orchestration compare with a linear workflow?"
            ),
            roles,
            forbidden,
            forbidden,
            lambda *args: events.append(args),
        )
        return result, usage, events

    result, usage, events = asyncio.run(main())
    assert result["status"] == "completed"
    assert result["report"]["demo_label"] == "Demo — synthetic evidence; no live research"
    assert {item["status"] for item in result["assessments"]} == {
        "supported",
        "conflicting",
        "insufficient",
    }
    source_map = {
        source["id"]: SourceDocument.model_validate(source) for source in result["sources"]
    }
    assert all(
        validate_passage(EvidencePassage.model_validate(item), source_map[item["source_id"]])
        for item in result["evidence"]
    )
    assert usage.model_requests <= 24
    assert events
    assert calls == []


def test_passage_offsets_reject_fabricated_excerpt():
    source = make_source(
        "https://example.org/doc", "Doc", "Exact passage from the source.", "text/html"
    )
    passage = make_passage(source, "Exact passage")
    assert passage is not None and validate_passage(passage, source)
    passage.excerpt = "invented text"
    assert not validate_passage(passage, source)
