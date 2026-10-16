import ast
import asyncio

import pytest
from langchain_core.messages import AIMessage

from app.budget import RunBudget
from app.config import Settings
from app.graph import run_research
from app.models import (
    CandidateClaim,
    Report,
    ResearchPlan,
    ResearchRequest,
    ResearchTask,
    VerificationOutput,
)
from app.providers import ProviderFailure, RoleAdapter


class MockModel:
    def with_structured_output(self, schema, **kwargs):
        class Structured:
            async def ainvoke(self, value, config):
                context = ast.literal_eval(
                    value.split("Request context: ", 1)[1].split("\nTask:", 1)[0]
                )
                if schema is ResearchPlan:
                    question = context["question"]
                    return ResearchPlan(
                        overview=question,
                        tasks=[
                            ResearchTask(
                                question=question,
                                search_queries=[question],
                                success_criteria=["Inspect evidence"],
                            )
                        ],
                    )
                if schema is CandidateClaim:
                    return CandidateClaim(text=context["text"])
                if schema is VerificationOutput:
                    return VerificationOutput(
                        assessments=[
                            {
                                "claim_id": claim["id"],
                                "status": "supported",
                                "supporting_evidence_ids": claim["candidate_evidence"],
                                "explanation": "Direct inspected support",
                            }
                            for claim in context["claims"]
                        ]
                    )
                if schema is Report:
                    return Report(
                        overview=context["question"],
                        findings=[
                            {
                                "heading": "Finding",
                                "text": claim["text"],
                                "claim_ids": [claim["claim_id"]],
                            }
                            for claim in context["assessments"]
                        ],
                    )
                raise AssertionError(schema)

        return Structured()

    def bind_tools(self, tools):
        class ToolModel:
            async def ainvoke(self, messages, config):
                count = sum(isinstance(m, AIMessage) for m in messages)
                question = messages[1].content.split("Subquestion: ", 1)[1].split("\n", 1)[0]
                calls = []
                if count == 0:
                    calls = [{"name": "search_web", "args": {"query": question}, "id": "search"}]
                elif count == 1:
                    calls = [
                        {
                            "name": "inspect_url",
                            "args": {"url": "https://example.org/source"},
                            "id": "fetch",
                        }
                    ]
                return AIMessage(content="", tool_calls=calls)

        return ToolModel()


def test_unrelated_live_questions_are_isolated_and_never_access_demo(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Live accessed demo fixtures")

    monkeypatch.setattr(RoleAdapter, "_demo_structured", forbidden)
    monkeypatch.setattr(RoleAdapter, "_demo_research", forbidden)
    monkeypatch.setattr(RoleAdapter, "_chat", lambda self: MockModel())

    async def scenario(question):
        queries, events = [], []

        async def search(query, domains):
            assert domains == []
            queries.append(query)
            return [{"url": "https://example.org/source"}]

        async def fetch(url):
            return {
                "status": "ok",
                "title": question,
                "url": url,
                "content_type": "text/html",
                "text": f"Inspected evidence for {question}",
            }

        adapter = RoleAdapter(
            Settings(_env_file=None, app_mode="live", openai_api_key="mock"), RunBudget("quick")
        )
        result, usage = await run_research(
            "run_test",
            ResearchRequest(question=question),
            adapter,
            search,
            fetch,
            lambda kind, *args: events.append(kind),
        )
        assert events.index("verification") < events.index("report")
        assert result["status"] == "completed"
        assert all(not source["synthetic"] for source in result["sources"])
        assert result["report"]["demo_label"] is None
        assert usage.metrics_scope == "live"
        return result, queries

    async def both():
        return await asyncio.gather(
            scenario("How does the Moon orbit Earth?"),
            scenario("How does retrieval augmented generation work?"),
        )

    (moon, moon_queries), (ai, ai_queries) = asyncio.run(both())
    assert moon["plan"] != ai["plan"]
    assert moon_queries != ai_queries
    assert moon["report"]["findings"] != ai["report"]["findings"]
    assert {s["id"] for s in moon["sources"]}.isdisjoint(s["id"] for s in ai["sources"])


def test_live_search_failure_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(RoleAdapter, "_chat", lambda self: MockModel())

    async def scenario():
        async def fail(*args):
            raise ProviderFailure("Tavily rejected the configured key or plan")

        adapter = RoleAdapter(Settings(_env_file=None, app_mode="live"), RunBudget("quick"))
        with pytest.raises(ProviderFailure, match="Tavily rejected"):
            await adapter.research_tool_call(
                ResearchTask(
                    question="How does the Moon orbit Earth?",
                    search_queries=[],
                    success_criteria=[],
                ),
                fail,
                fail,
                [],
                lambda *args: None,
            )

    asyncio.run(scenario())
