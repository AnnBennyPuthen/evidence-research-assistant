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
    url: str, title: str, text: str, content_type: str, *, synthetic: bool = False
) -> SourceDocument:
    normalized = " ".join(text.split())
    host = urlsplit(url).hostname or "synthetic.invalid"
    return SourceDocument(
        canonical_url=url,
        title=title,
        domain=host,
        content_type=content_type,
        normalized_text=normalized,
        content_hash=hashlib.sha256(normalized.encode()).hexdigest(),
        synthetic=synthetic,
        extraction_status="ok" if normalized else "failed",
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
