import asyncio
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite, Send

from .evidence import make_passage, make_source
from .models import (
    CandidateClaim,
    ClaimAssessment,
    EvidencePassage,
    Report,
    ReportFinding,
    ReportWordingOutput,
    ResearchPlan,
    ResearchRequest,
    ResearchTask,
    SourceDocument,
    Usage,
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


def sanitize_report(
    report: Report, assessments: list[ClaimAssessment], sources: list[dict]
) -> Report:
    assessment_by_id = {item.claim_id: item for item in assessments}
    valid_claim_ids = set(assessment_by_id)
    valid_source_ids = {item["id"] for item in sources}

    def clean(findings: list[ReportFinding]) -> list[ReportFinding]:
        accepted = []
        for finding in findings:
            finding.claim_ids = [ident for ident in finding.claim_ids if ident in valid_claim_ids]
            finding.premise_claim_ids = [
                ident for ident in finding.premise_claim_ids if ident in valid_claim_ids
            ]
            if finding.kind == "evidence":
                if not finding.claim_ids:
                    continue
                finding.text = assessment_by_id[finding.claim_ids[0]].text
            elif not finding.premise_claim_ids:
                continue
            accepted.append(finding)
        return accepted

    report.findings = clean(report.findings)
    report.comparison = clean(report.comparison)
    report.conflicts = clean(report.conflicts)
    report.source_ids = [ident for ident in report.source_ids if ident in valid_source_ids]
    return report


def build_graph(roles: RoleAdapter, search, fetch, add_event):
    researcher_semaphore = asyncio.Semaphore(2)

    async def planner(state: State):
        req = ResearchRequest.model_validate(state["request"])
        plan = await roles.structured(
            "Planner", ResearchPlan, req.question, {"question": req.question}
        )
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
            if item.get("tool_output"):
                lines = item["tool_output"].splitlines()
                text = item["tool_output"].split("TEXT: ", 1)[-1]
                title = next(
                    (line.removeprefix("TITLE: ") for line in lines if line.startswith("TITLE: ")),
                    "Untitled source",
                )
                url = next(
                    (line.removeprefix("URL: ") for line in lines if line.startswith("URL: ")), ""
                )
                content_type = next(
                    (
                        line.removeprefix("CONTENT_TYPE: ")
                        for line in lines
                        if line.startswith("CONTENT_TYPE: ")
                    ),
                    "text/html",
                )
                page_number = (
                    1 if content_type == "application/pdf" and "[Page 1]" in text else None
                )
                source = make_source(url, title, text, content_type)
                excerpt = text[:5000]
                fixture = None
            else:
                if roles.settings.app_mode == "live":
                    raise ProviderFailure(
                        "Live research returned an invalid source; synthetic sources are forbidden"
                    )
                source = make_source(
                    item["url"], item["title"], item["text"], "text/plain", synthetic=True
                )
                excerpt = item["text"]
                fixture = item.get("fixture")
            passage = make_passage(source, excerpt)
            if not passage:
                continue
            passage.page_number = page_number if item.get("tool_output") else None
            if roles.settings.app_mode == "demo":
                claim = await roles.structured(
                    "Researcher",
                    CandidateClaim,
                    "Extract one atomic candidate claim.",
                    {"demo_text": item["text"], "fixture": fixture},
                )
            else:
                claim = await roles.structured(
                    "Researcher",
                    CandidateClaim,
                    "Extract one atomic claim relevant to the question, supported by this inspected passage. Do not include absent details.",
                    {
                        "question": state.get("question", task.question),
                        "subquestion": task.question,
                        "text": excerpt,
                    },
                )
            claim.evidence_ids = [passage.id]
            sources.append(source.model_dump(mode="json"))
            passages.append(passage.model_dump(mode="json"))
            candidates.append({**claim.model_dump(), "fixture": fixture})
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
            output = await roles.structured(
                "Evidence Verifier",
                VerificationOutput,
                "Assess each atomic claim against only the supplied inspected passages. Use only supplied claim/evidence IDs. "
                "A supported assessment requires direct support; conflicting requires both support and counterevidence, and "
                "otherwise use insufficient. Note date, version, and scope limitations. Request follow-up questions only for material gaps.",
                prompt_context,
            )
            verified = []
            candidate_by_id = {item["id"]: item for item in candidates}
            usable_ids = {
                p.id for p in passage_map.values() if p.usable and p.source_id in source_map
            }
            for draft in output.assessments:
                candidate = candidate_by_id.get(draft.claim_id)
                if not candidate:
                    continue
                support = [
                    ident
                    for ident in draft.supporting_evidence_ids
                    if ident in usable_ids and ident in candidate["evidence_ids"]
                ]
                contradict = [
                    ident for ident in draft.contradicting_evidence_ids if ident in usable_ids
                ]
                status = draft.status
                if status == "supported" and not support:
                    status = "insufficient"
                if status == "conflicting" and not (support and contradict):
                    status = "insufficient"
                verified.append(
                    ClaimAssessment(
                        claim_id=candidate["id"],
                        text=candidate["text"],
                        status=status,
                        supporting_evidence_ids=support,
                        contradicting_evidence_ids=contradict,
                        explanation=draft.explanation,
                    ).model_dump()
                )
            assessed_ids = {item["claim_id"] for item in verified}
            verified.extend(
                ClaimAssessment(
                    claim_id=candidate["id"],
                    text=candidate["text"],
                    status="insufficient",
                    explanation="The verifier did not return an assessment for this candidate.",
                ).model_dump()
                for candidate in candidates
                if candidate["id"] not in assessed_ids
            )
            results = verified
            max_rounds = roles.budget.limits["followups"]
            round_number = state.get("followup_round", 0)
            followups = []
            if round_number < max_rounds:
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
        if followups:
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
        assessments_by_id = {
            item["claim_id"]: ClaimAssessment.model_validate(item)
            for item in state.get("assessments", [])
        }
        assessments = list(assessments_by_id.values())
        evidence_claims = [
            ReportFinding(
                heading="Supported by retrieved evidence",
                text=item.text,
                claim_ids=[item.claim_id],
            )
            for item in assessments
            if item.status == "supported"
        ]
        conflicts = [
            ReportFinding(heading="Conflicting evidence", text=item.text, claim_ids=[item.claim_id])
            for item in assessments
            if item.status == "conflicting"
        ]
        gaps = [item.text for item in assessments if item.status == "insufficient"]
        req = ResearchRequest.model_validate(state["request"])
        demo_label = (
            "Demo — synthetic evidence; no live research"
            if roles.settings.app_mode == "demo"
            else None
        )
        if roles.settings.app_mode == "live":
            report = await roles.structured(
                "Report Writer",
                Report,
                "Write a bounded structured report. Every factual finding must use supplied assessed claim IDs; synthesis/recommendations must state premises. "
                "Do not add URLs, dates, quotes, or claims. Do not treat insufficient/conflicting assessments as established findings.",
                {
                    "question": req.question,
                    "assessments": [item.model_dump() for item in assessments],
                    "sources": [
                        {"id": item["id"], "title": item["title"], "domain": item["domain"]}
                        for item in state.get("sources", [])
                    ],
                },
            )
            report.demo_label = None
        else:
            report = Report(
                overview="Fixed synthetic LangGraph example. Arbitrary question input does not produce real research in demo mode.",
                findings=evidence_claims,
                comparison=conflicts,
                conflicts=conflicts,
                gaps=gaps or ["Live source dates and version coverage require further checking."],
                source_ids=list(dict.fromkeys(item["id"] for item in state.get("sources", []))),
                demo_label=demo_label,
            )
        if roles.settings.app_mode == "demo":
            report.overview = "Fixed synthetic LangGraph example. Arbitrary question input does not produce real research in demo mode."
        interpretation_findings = [
            (index, finding)
            for index, finding in enumerate(report.findings + report.comparison + report.conflicts)
            if finding.kind != "evidence"
        ]
        if roles.settings.app_mode == "live" and interpretation_findings:
            context = [
                {
                    "finding_index": index,
                    "text": finding.text,
                    "claim_ids": finding.claim_ids,
                    "premise_claim_ids": finding.premise_claim_ids,
                }
                for index, finding in interpretation_findings
            ]
            context.append(
                {
                    "evidence": [
                        {"id": item.id, "excerpt": item.excerpt}
                        for item in (
                            EvidencePassage.model_validate(raw) for raw in state.get("evidence", [])
                        )
                    ]
                }
            )
            try:
                wording = await roles.structured(
                    "Evidence Verifier",
                    ReportWordingOutput,
                    "Review only the supplied report wording against its linked assessed premises and inspected evidence. "
                    "Assess whether the exact wording is supported, conflicting, or insufficient. Do not request searches or follow-up work.",
                    {"report_items": context},
                )
                valid_indices = {
                    index
                    for index, finding in interpretation_findings
                    if (finding.kind == "synthesis" and finding.premise_claim_ids)
                    or (finding.kind == "recommendation" and finding.premise_claim_ids)
                }
                accepted_indices = {
                    item.finding_index
                    for item in wording.assessments
                    if item.finding_index in valid_indices
                    and item.status in {"supported", "conflicting"}
                }
                all_findings = report.findings + report.comparison + report.conflicts
                keep = {
                    index
                    for index, finding in enumerate(all_findings)
                    if finding.kind == "evidence" or index in accepted_indices
                }
                report.findings = [
                    item
                    for index, item in enumerate(all_findings)
                    if index in keep and item in report.findings
                ]
                report.comparison = [
                    item
                    for index, item in enumerate(all_findings)
                    if index in keep and item in report.comparison
                ]
                report.conflicts = [
                    item
                    for index, item in enumerate(all_findings)
                    if index in keep and item in report.conflicts
                ]
                if len(accepted_indices) < len(interpretation_findings):
                    report.gaps.append(
                        "Some synthesis or recommendation wording was omitted after the final evidence check."
                    )
            except ProviderFailure:
                raise
            except Exception:
                report.findings = [item for item in report.findings if item.kind == "evidence"]
                report.comparison = [item for item in report.comparison if item.kind == "evidence"]
                report.conflicts = [item for item in report.conflicts if item.kind == "evidence"]
                report.gaps.append(
                    "Synthesis was omitted because the final wording check could not be completed within the run budget."
                )
        report = sanitize_report(report, assessments, state.get("sources", []))
        if roles.settings.app_mode == "live" and not state.get("evidence"):
            report.gaps.append(
                "No usable source passages were retrieved; this run cannot establish an answer."
            )
        add_event("report", "Structured report is ready", {})
        return {"report": report.model_dump(mode="json"), "status": "completed"}

    builder = StateGraph(State)
    builder.add_node("planner", planner)
    builder.add_node("researcher", researcher)
    builder.add_node("merge_findings", merge_findings)
    builder.add_node("verifier", verifier)
    builder.add_node("writer", writer)
    builder.add_edge(START, "planner")
    builder.add_conditional_edges("planner", dispatch, ["researcher"])
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
    budget = roles.budget
    demo = roles.settings.app_mode == "demo"
    usage = Usage(
        model_requests=0 if demo else budget.counts["models"],
        scripted_model_steps=budget.counts["models"] if demo else 0,
        metrics_scope="demo" if demo else "live",
        input_tokens=roles.input_tokens,
        output_tokens=roles.output_tokens,
        search_requests=budget.counts["searches"],
        fetch_attempts=budget.counts["fetches"],
        cached_calls=budget.counts["cached_calls"],
        ended_by_limit=budget.ended_by_limit,
    )
    return result, usage
