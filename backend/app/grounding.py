"""Deterministic report boundaries; semantic interpretation is separately checked."""
from collections import Counter

from .models import ClaimAssessment, Report, ReportFinding, VerificationOutput


def validate_assessments(output: VerificationOutput, candidates: list[dict], passages: list[dict]):
    counts = Counter(item.claim_id for item in output.assessments)
    drafts = {item.claim_id: item for item in output.assessments}
    usable = {p["id"] for p in passages if p.get("usable", True)}
    results = []
    for candidate in candidates:
        draft = drafts.get(candidate["id"])
        support, counter = [], []
        status, explanation = "insufficient", "Missing or duplicate verifier assessment."
        if draft and counts[candidate["id"]] == 1:
            raw_support = set(draft.supporting_evidence_ids)
            raw_counter = set(draft.contradicting_evidence_ids)
            overlap = raw_support & raw_counter
            invalid = (raw_support | raw_counter) - usable
            support = sorted(raw_support & usable - overlap)
            counter = sorted(raw_counter & usable - overlap)
            status, explanation = draft.status, draft.explanation
            if overlap or invalid or (status == "supported" and (not support or counter)) or (
                status == "conflicting" and not (support and counter)
            ):
                status, explanation = "insufficient", "Malformed assessment: invalid, overlapping, or missing evidence."
        results.append(ClaimAssessment(claim_id=candidate["id"], text=candidate["text"], status=status,
                                       supporting_evidence_ids=support, contradicting_evidence_ids=counter,
                                       explanation=explanation).model_dump())
    return results


def sanitize_report(report: Report, assessments: list[ClaimAssessment], sources: list[dict],
                    *, allow_interpretation: bool = False) -> Report:
    counts = Counter(a.claim_id for a in assessments)
    claims = {a.claim_id: a for a in assessments if counts[a.claim_id] == 1}
    sections = {"findings": [], "comparison": [], "conflicts": []}
    seen = set()
    for section in sections:
        for finding in getattr(report, section):
            refs = finding.claim_ids + finding.premise_claim_ids
            if any(ident not in claims for ident in refs):
                continue
            if finding.kind == "evidence":
                for ident in dict.fromkeys(finding.claim_ids):
                    assessment = claims[ident]
                    if assessment.status == "insufficient" or ident in seen:
                        continue
                    target = "conflicts" if assessment.status == "conflicting" else section
                    if target == "conflicts" and assessment.status == "supported":
                        target = "findings"
                    sections[target].append(ReportFinding(
                        heading="Conflicting evidence" if target == "conflicts" else "Supported by retrieved evidence",
                        text=assessment.text, claim_ids=[ident], qualification=assessment.status))
                    seen.add(ident)
            elif allow_interpretation and finding.premise_claim_ids:
                statuses = {claims[i].status for i in finding.premise_claim_ids}
                if "insufficient" in statuses:
                    continue
                finding.heading = "Qualified interpretation" if finding.kind == "synthesis" else "Qualified recommendation"
                if "conflicting" in statuses or finding.qualification == "conflicting":
                    finding.qualification = "conflicting"
                target = "conflicts" if finding.qualification == "conflicting" else "comparison"
                sections[target].append(finding)
    for section, findings in sections.items():
        setattr(report, section, findings)
    totals = Counter(a.status for a in claims.values())
    report.overview = (f"Retrieved evidence assessment: {totals['supported']} supported, "
                       f"{totals['conflicting']} conflicting, {totals['insufficient']} insufficient claims. "
                       "Interpretations are qualified and limited to inspected sources.")
    report.gaps = [f"Not established by retrieved evidence: {a.text}" for a in claims.values()
                   if a.status == "insufficient"]
    if not claims:
        report.gaps.append("No assessed claims are available; no answer is established.")
    report.source_ids = list(dict.fromkeys(s["id"] for s in sources))
    return report


def deterministic_report(assessments: list[ClaimAssessment], sources: list[dict], *, partial=False):
    report = Report(overview="", findings=[ReportFinding(heading="", text=a.text, claim_ids=[a.claim_id])
                                            for a in assessments])
    report = sanitize_report(report, assessments, sources)
    if partial:
        report.overview = "Partial investigation. " + report.overview
        report.gaps.append("The run budget stopped further research or final verification. Unassessed passages are not established findings.")
    return report


def synthesis_context(report: Report, assessments: list[ClaimAssessment], passages: list[dict], sources: list[dict]):
    claims = {a.claim_id: a for a in assessments}
    evidence = {p["id"]: p for p in passages}
    source_map = {s["id"]: s for s in sources}
    items = []
    for section in ("findings", "comparison", "conflicts"):
        for finding in getattr(report, section):
            if finding.kind == "evidence":
                continue
            premises = []
            for ident in finding.premise_claim_ids:
                assessment = claims[ident]
                linked = []
                for side, ids in (("support", assessment.supporting_evidence_ids),
                                  ("counterevidence", assessment.contradicting_evidence_ids)):
                    for eid in ids:
                        passage = evidence.get(eid)
                        if not passage:
                            continue
                        source = source_map.get(passage["source_id"], {})
                        linked.append({"side": side, "passage": passage, "source": {
                            key: source.get(key) for key in ("id", "canonical_url", "title", "domain", "content_type")}})
                premises.append({"assessment": assessment.model_dump(), "evidence": linked})
            items.append({"finding_index": len(items), "section": section,
                          "finding": finding.model_dump(), "premises": premises})
    return items
