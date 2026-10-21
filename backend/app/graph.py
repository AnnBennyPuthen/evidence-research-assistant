import asyncio
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite, Send

from .budget import BudgetExhausted
from .evidence import make_passage, make_source, select_passages
from .grounding import deterministic_report, sanitize_report, synthesis_context, validate_assessments
from .models import (
    CandidateClaim,
    CandidateClaimDraft,
    ClaimAssessment,
    EvidencePassage,
    Report,
    ReportFinding,
    ReportWordingOutput,
    ResearchPlan,
    ResearchPlanDraft,
    ResearchRequest,
    ResearchTask,
    SourceDocument,
    new_id,
    VerificationOutput,
)
from .providers import ProviderFailure, RoleAdapter


class State(TypedDict, total=False):
    run_id: str
    request: dict
    plan: dict
    sources: Annotated[list[dict], operator.add]
    evidence: Annotated[list[dict], operator.add]
    candidates: Annotated[list[dict], operator.add]
    assessments: Annotated[list[dict], operator.add]
    report: dict
    events: Annotated[list[dict], operator.add]
    followup_round: int
    followups: list[dict]
    usage: dict
    status: str


def build_graph(roles: RoleAdapter, search, fetch, add_event):
    researcher_semaphore = asyncio.Semaphore(2)

    async def planner(state: State):
        req = ResearchRequest.model_validate(state["request"])
        try:
            draft = await roles.structured(
                "Planner", ResearchPlanDraft, req.question, {"question": req.question}
            )
            draft = ResearchPlanDraft.model_validate(draft.model_dump())
            plan = ResearchPlan(overview=draft.overview, comparison_dimensions=draft.comparison_dimensions,
                                tasks=[ResearchTask(**task.model_dump()) for task in draft.tasks])
        except BudgetExhausted:
            return {"plan": None}
        max_tasks = roles.budget.limits["tasks"]
        plan.tasks = plan.tasks[:max_tasks]
        add_event(
            "plan",
            f"Plan created with {len(plan.tasks)} independent subquestions",
            {"plan": plan.model_dump()},
        )
        return {"plan": plan.model_dump(), "events": [{"kind": "plan", "message": plan.overview}]}

    def dispatch(state: State):
        req = ResearchRequest.model_validate(state["request"])
        if not state.get("plan"):
            return "writer"
        return [
            Send(
                "researcher",
                {
                    "run_id": state["run_id"],
                    "task": task,
                    "question": req.question,
                    "preferred_domains": req.preferred_domains,
                },
            )
            for task in state["plan"]["tasks"]
        ]

    async def researcher(state: State):
        task = ResearchTask.model_validate(state["task"])
        add_event("research", f"Researching: {task.question}", {"task_id": task.id})
        async with researcher_semaphore:
            items = await roles.research_tool_call(
                task, search, fetch, state.get("preferred_domains", []), add_event
            )
        sources, passages, candidates = [], [], []
        for item in items:
            demo = roles.settings.app_mode == "demo"
            if not demo and (item.get("fixture") or item.get("synthetic") or not item.get("text")):
                raise ProviderFailure("Invalid live source record; synthetic sources are forbidden")
            source = make_source(item["url"], item["title"], item["text"],
                                 item.get("content_type", "text/plain"), synthetic=demo, normalized=not demo)
            sources.append(source.model_dump(mode="json"))
            selected = ([make_passage(source, source.normalized_text)] if demo else
                        select_passages(source, task.question, item.get("page_ranges", [])))
            for passage in selected:
                if passage is None:
                    raise ProviderFailure("Inspected source did not yield an exact evidence passage")
                passages.append(passage.model_dump(mode="json"))
                try:
                    draft = await roles.structured(
                        "Researcher", CandidateClaimDraft,
                        "Extract one atomic claim relevant to the question, supported by this inspected passage. Do not include absent details.",
                        {"question": state.get("question", task.question), "subquestion": task.question,
                         "text": passage.excerpt, **({"demo_text": item["text"]} if demo else {})},
                    )
                    draft = CandidateClaimDraft.model_validate(draft.model_dump())
                except BudgetExhausted:
                    continue
                claim = CandidateClaim(text=draft.text, evidence_ids=[passage.id])
                candidates.append({**claim.model_dump(), "fixture": item.get("fixture") if demo else None})
        return {
            "sources": sources,
            "evidence": passages,
            "candidates": candidates,
            "events": [
                {"kind": "research", "message": f"Collected {len(passages)} inspected passages"}
            ],
        }

    def merge_findings(state: State):
        source_by_key = {}
        sources = []
        passage_by_key = {}
        passages = []
        id_remap = {}
        source_remap = {}
        for raw in state.get("sources", []):
            source = SourceDocument.model_validate(raw)
            key = (str(source.canonical_url), source.content_hash)
            if key in source_by_key:
                source_remap[source.id] = source_by_key[key].id
            else:
                source_by_key[key] = source
                sources.append(source.model_dump(mode="json"))
                source_remap[source.id] = source.id
        for raw in state.get("evidence", []):
            passage = EvidencePassage.model_validate(raw)
            parent = next(
                (
                    source
                    for source in state.get("sources", [])
                    if source["id"] == passage.source_id
                ),
                None,
            )
            if not parent:
                continue
            source = SourceDocument.model_validate(parent)
            key = (str(source.canonical_url), passage.excerpt)
            if key in passage_by_key:
                id_remap[passage.id] = passage_by_key[key].id
                continue
            passage.source_id = source_remap.get(passage.source_id, passage.source_id)
            passage_by_key[key] = passage
            id_remap[raw["id"]] = passage.id
            passages.append(passage.model_dump(mode="json"))
        candidates = []
        candidate_keys = set()
        candidate_ids = set()
        for candidate in state.get("candidates", []):
            candidate["evidence_ids"] = list(
                dict.fromkeys(
                    id_remap[ident]
                    for ident in candidate.get("evidence_ids", [])
                    if ident in id_remap
                )
            )
            key = (" ".join(candidate["text"].split()).casefold(), tuple(candidate["evidence_ids"]))
            if candidate["evidence_ids"] and key not in candidate_keys:
                candidate_keys.add(key)
                if candidate["id"] in candidate_ids:
                    candidate["id"] = new_id("claim")
                candidate_ids.add(candidate["id"])
                candidates.append(candidate)
        add_event(
            "merge", f"Merged parallel results into {len(sources)} unique inspected sources", {}
        )
        return {
            "sources": Overwrite(sources),
            "evidence": Overwrite(passages),
            "candidates": Overwrite(candidates),
        }

    async def verifier(state: State):
        source_map = {
            item["id"]: SourceDocument.model_validate(item) for item in state.get("sources", [])
        }
        passage_map = {
            item["id"]: EvidencePassage.model_validate(item) for item in state.get("evidence", [])
        }
        candidates = state.get("candidates", [])
        results = []
        if roles.settings.app_mode == "live" and candidates:
            prompt_context = {
                "question": state["request"]["question"],
                "claims": [
                    {"id": c["id"], "text": c["text"], "candidate_evidence": c["evidence_ids"]}
                    for c in candidates
                ],
                "passages": [
                    {"id": passage.id, "source_id": passage.source_id, "excerpt": passage.excerpt}
                    for passage in passage_map.values()
                ],
                "source_context": [
                    {"id": source.id, "url": str(source.canonical_url), "title": source.title}
                    for source in source_map.values()
                ],
            }
            try:
                output = await roles.structured(
                    "Evidence Verifier", VerificationOutput,
                    "Assess each atomic claim against the supplied inspected passages. Return exactly one assessment per claim. "
                    "Support and counterevidence must be different passage IDs. Conflicting requires both sides. "
                    "Otherwise use insufficient. Request follow-up only for material gaps.", prompt_context,
                )
                results = validate_assessments(output, candidates, state.get("evidence", []))
            except BudgetExhausted:
                previous = {a["claim_id"]: a for a in state.get("assessments", [])}
                results = [previous.get(c["id"], ClaimAssessment(
                    claim_id=c["id"], text=c["text"], status="insufficient",
                    explanation="Verification unavailable within the remaining budget."
                ).model_dump()) for c in candidates]
                output = VerificationOutput(assessments=[])
            max_rounds = roles.budget.limits["followups"]
            round_number = state.get("followup_round", 0)
            followups = []
            if round_number < max_rounds and not roles.budget.ended_by_limit:
                for question in output.follow_up_questions[:3]:
                    followups.append(
                        ResearchTask(
                            question=question,
                            search_queries=[question],
                            success_criteria=["Inspect a source that resolves the identified gap"],
                        ).model_dump()
                    )
            add_event("verification", f"Assessed {len(results)} candidate claims", {})
            return {
                "assessments": Overwrite(results),
                "followups": followups,
                "followup_round": round_number + (1 if followups else 0),
            }
        elif roles.settings.app_mode == "demo":
            # Demo fixtures deliberately exercise supported, conflicting, and insufficient outcomes.
            for candidate in candidates:
                fixture = candidate.get("fixture")
                ev_ids = [
                    ev_id
                    for ev_id in candidate["evidence_ids"]
                    if ev_id in passage_map
                    and passage_map[ev_id].source_id in source_map
                    and make_passage(
                        source_map[passage_map[ev_id].source_id], passage_map[ev_id].excerpt
                    )
                ]
                if fixture == "official":
                    status, support, contradict, explanation = (
                        "supported",
                        ev_ids,
                        [],
                        "The inspected synthetic passage directly states this bounded claim.",
                    )
                elif fixture == "independent":
                    status, support, contradict, explanation = (
                        "supported",
                        ev_ids,
                        [],
                        "The inspected synthetic passage directly supports this qualified implementation trade-off.",
                    )
                elif fixture == "conflict":
                    support_ids = next(
                        (
                            related["evidence_ids"]
                            for related in candidates
                            if related.get("fixture") == "official"
                        ),
                        [],
                    )
                    status, support, contradict, explanation = (
                        "conflicting",
                        support_ids,
                        ev_ids,
                        "The inspected synthetic comparison gives a conditional alternative; evidence does not establish one universally better approach.",
                    )
                else:
                    status, support, contradict, explanation = (
                        "insufficient",
                        [],
                        [],
                        "No usable evidence passage was linked to this candidate.",
                    )
                results.append(
                    ClaimAssessment(
                        claim_id=candidate["id"],
                        text=candidate["text"],
                        status=status,
                        supporting_evidence_ids=support,
                        contradicting_evidence_ids=contradict,
                        explanation=explanation,
                    ).model_dump()
                )
        add_event(
            "verification",
            f"Assessed {len(results)} candidate claims",
            {"statuses": [r["status"] for r in results]},
        )
        return {
            "assessments": results,
            "followups": [],
            "followup_round": state.get("followup_round", 0),
        }

    def route_after_verify(state: State):
        followups = state.get("followups", [])
        if followups and not roles.budget.ended_by_limit:
            request = ResearchRequest.model_validate(state["request"])
            return [
                Send(
                    "researcher",
                    {
                        "run_id": state["run_id"],
                        "task": task,
                        "question": request.question,
                        "preferred_domains": request.preferred_domains,
                    },
                )
                for task in followups
            ]
        return "writer"

    async def writer(state: State):
        assessments = [ClaimAssessment.model_validate(a) for a in state.get("assessments", [])]
        sources = state.get("sources", [])
        demo = roles.settings.app_mode == "demo"
        partial = bool(roles.budget.ended_by_limit)
        report = deterministic_report(assessments, sources, partial=partial)
        if not demo:
            try:
                draft = await roles.structured(
                    "Report Writer", Report,
                    "Write a bounded report using assessed claims. Established findings require supported claims. "
                    "Keep conflicting evidence separate; interpretations need premise claim IDs and qualifications.",
                    {"question": state["request"]["question"],
                     "assessments": [a.model_dump() for a in assessments],
                     "sources": [{k: source[k] for k in ("id", "title", "domain")} for source in sources]},
                )
                draft.demo_label = None
                # Reject invalid references/statuses and constrain free text before semantic checking.
                draft = sanitize_report(draft, assessments, sources, allow_interpretation=True)
                items = synthesis_context(draft, assessments, state.get("evidence", []), sources)
                if items:
                    try:
                        wording = await roles.structured(
                            "Evidence Verifier", ReportWordingOutput,
                            "Check exact interpretation wording against the full assessed premises and their linked "
                            "support/counterevidence. Preserve uncertainty. Return one assessment per finding_index. "
                            "Unsupported generalizations are insufficient; disputed interpretations are conflicting.",
                            {"question": state["request"]["question"], "report_items": items},
                        )
                        by_index = {}
                        for assessment in wording.assessments:
                            by_index.setdefault(assessment.finding_index, []).append(assessment)
                        index = 0
                        for section in ("findings", "comparison", "conflicts"):
                            accepted = []
                            for finding in getattr(draft, section):
                                if finding.kind == "evidence":
                                    accepted.append(finding)
                                    continue
                                checks = by_index.get(index, [])
                                index += 1
                                if len(checks) == 1 and checks[0].status in {"supported", "conflicting"}:
                                    finding.qualification = ("conflicting" if finding.qualification == "conflicting"
                                                             else checks[0].status)
                                    accepted.append(finding)
                            setattr(draft, section, accepted)
                    except BudgetExhausted:
                        for section in ("findings", "comparison", "conflicts"):
                            setattr(draft, section, [f for f in getattr(draft, section) if f.kind == "evidence"])
                report = sanitize_report(draft, assessments, sources, allow_interpretation=True)
            except BudgetExhausted:
                report = deterministic_report(assessments, sources, partial=True)
            partial = bool(roles.budget.ended_by_limit)
            if partial and not report.overview.startswith("Partial"):
                report.overview = "Partial investigation. " + report.overview
                report.gaps.append("Run budget reached; further research or interpretation checks were omitted.")
        else:
            report.demo_label = "Demo ? synthetic evidence; no live research"
            report.overview = "Fixed synthetic LangGraph example. Arbitrary question input does not produce real research in demo mode."
        add_event("report", "Partial report is ready" if partial else "Structured report is ready", {})
        return {"report": report.model_dump(mode="json"), "status": "partial" if partial else "completed"}

    builder = StateGraph(State)
    builder.add_node("planner", planner)
    builder.add_node("researcher", researcher)
    builder.add_node("merge_findings", merge_findings)
    builder.add_node("verifier", verifier)
    builder.add_node("writer", writer)
    builder.add_edge(START, "planner")
    builder.add_conditional_edges("planner", dispatch, ["researcher", "writer"])
    builder.add_edge("researcher", "merge_findings")
    builder.add_edge("merge_findings", "verifier")
    builder.add_conditional_edges("verifier", route_after_verify, ["researcher", "writer"])
    builder.add_edge("writer", END)
    return builder.compile()


async def run_research(
    run_id: str,
    request: ResearchRequest,
    roles: RoleAdapter,
    search,
    fetch,
    add_event,
    on_update=None,
):
    graph = build_graph(roles, search, fetch, add_event)
    from langgraph.types import Overwrite

    initial = {
        "run_id": run_id,
        "request": request.model_dump(),
        "sources": [],
        "evidence": [],
        "candidates": [],
        "events": [],
        "followup_round": 0,
    }
    result = dict(initial)
    async for update in graph.astream(initial, stream_mode="updates"):
        for node_update in update.values():
            for key, value in node_update.items():
                if isinstance(value, Overwrite):
                    result[key] = value.value
                elif key in {"sources", "evidence", "candidates", "events"}:
                    result[key] = result.get(key, []) + value
                else:
                    result[key] = value
        if on_update:
            await on_update(result)
    return result, roles.usage()
