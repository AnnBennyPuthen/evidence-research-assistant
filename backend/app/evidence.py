import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from .models import EvidencePassage, SourceDocument


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host + port, path, parts.query, ""))


def make_source(
    url: str, title: str, text: str, content_type: str, *, synthetic: bool = False,
    normalized: bool = False,
) -> SourceDocument:
    content = text if normalized else " ".join(text.split())
    host = urlsplit(url).hostname or "synthetic.invalid"
    return SourceDocument(
        canonical_url=url,
        title=title,
        domain=host,
        content_type=content_type,
        normalized_text=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        synthetic=synthetic,
        extraction_status="ok" if content else "failed",
    )


def make_passage(source: SourceDocument, excerpt: str) -> EvidencePassage | None:
    start = source.normalized_text.find(excerpt)
    if start < 0 or source.extraction_status == "failed":
        return None
    return EvidencePassage(
        source_id=source.id,
        excerpt=excerpt,
        start_offset=start,
        end_offset=start + len(excerpt),
    )


def validate_passage(passage: EvidencePassage, source: SourceDocument) -> bool:
    return (
        passage.source_id == source.id
        and passage.end_offset <= len(source.normalized_text)
        and source.normalized_text[passage.start_offset : passage.end_offset] == passage.excerpt
        and source.extraction_status != "failed"
    )


def normalize_record(record: dict) -> dict:
    """Normalize once, retaining exact PDF page ranges in the document."""
    result = dict(record)
    ranges, chunks, offset = [], [], 0
    for page in record.get("pages", []):
        text = " ".join(page["text"].split())
        if not text:
            continue
        if chunks:
            offset += 1
        ranges.append({"start": offset, "end": offset + len(text), "page_number": page["page_number"]})
        chunks.append(text)
        offset += len(text)
    result["text"] = " ".join(chunks) if chunks else " ".join(record["text"].split())
    result["page_ranges"] = ranges
    return result


def select_passages(source: SourceDocument, question: str, ranges: list[dict], limit: int = 5000):
    """Rank bounded windows over the whole retrieved document, not just its prefix."""
    text = source.normalized_text
    terms = {word for word in re.findall(r"\w+", question.lower()) if len(word) > 3}
    regions = ranges or [{"start": 0, "end": len(text), "page_number": None}]
    windows = []
    for region in regions:
        for start in range(region["start"], region["end"], 1200):
            end = min(start + 1500, region["end"])
            excerpt = text[start:end].strip()
            if not excerpt:
                continue
            start += len(text[start:end]) - len(text[start:end].lstrip())
            score = sum(excerpt.lower().count(term) for term in terms)
            windows.append((score, start, excerpt, region["page_number"]))
    selected, remaining = [], limit
    for _, start, excerpt, page in sorted(windows, key=lambda w: (-w[0], w[1])):
        if any(start < p.end_offset and start + len(excerpt) > p.start_offset for p in selected):
            continue
        excerpt = excerpt[:remaining].rstrip()
        if not excerpt:
            break
        selected.append(EvidencePassage(source_id=source.id, excerpt=excerpt, start_offset=start,
                                        end_offset=start + len(excerpt), page_number=page))
        remaining -= len(excerpt)
        if remaining <= 0 or len(selected) >= 3:
            break
    return sorted(selected, key=lambda p: p.start_offset)
