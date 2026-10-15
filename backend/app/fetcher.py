import asyncio
import io
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader


class UnsafeURL(ValueError):
    pass


def _public_addresses(host: str) -> list[str]:
    try:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeURL("Hostname could not be resolved") from exc
    addresses = list({answer[4][0] for answer in answers})
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise UnsafeURL("Private, reserved, and local network addresses are not fetchable")
    return addresses


async def validate_public_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise UnsafeURL("Only credential-free HTTP(S) URLs are allowed")
    host = parts.hostname.rstrip(".").lower()
    if (
        host in {"localhost", "localhost.localdomain"}
        or host.endswith(".localhost")
        or host.endswith(".local")
    ):
        raise UnsafeURL("Local hostnames are not fetchable")
    await asyncio.to_thread(_public_addresses, host)
    return url


async def fetch_public(url: str, *, max_bytes: int, max_pages: int, max_chars: int) -> dict:
    current = url
    headers = {"User-Agent": "EvidenceBasedResearchAssistant/0.1 (local portfolio project)"}
    timeout = httpx.Timeout(12, connect=5)
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, verify=True, trust_env=False
    ) as client:
        for redirect_count in range(4):
            await validate_public_url(current)
            async with client.stream("GET", current, headers=headers) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirect_count == 3:
                        return {"status": "failed", "reason": "Redirect limit reached"}
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        return {"status": "failed", "reason": "Response exceeded byte limit"}
                    chunks.append(chunk)
                body = b"".join(chunks)
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                final_url = str(response.url)
                title = "Untitled source"
                page_number = None
                if content_type == "application/pdf" or body[:4] == b"%PDF":

                    def parse_pdf():
                        reader = PdfReader(io.BytesIO(body), strict=False)
                        return [
                            f"[Page {page_index + 1}] {page.extract_text() or ''}"
                            for page_index, page in enumerate(reader.pages[:max_pages])
                        ]

                    page_text = await asyncio.wait_for(asyncio.to_thread(parse_pdf), timeout=12)
                    text = " ".join(" ".join(page_text).split())
                    content_type = "application/pdf"
                    title = urlsplit(final_url).path.rsplit("/", 1)[-1] or "Text PDF"
                elif content_type in {"text/html", "application/xhtml+xml", "text/plain"}:
                    decoded = body.decode("utf-8", errors="replace")
                    if content_type == "text/plain":
                        text = " ".join(decoded.split())
                    else:
                        soup = BeautifulSoup(decoded, "html.parser")
                        title = soup.title.get_text(" ", strip=True) if soup.title else title
                        for element in soup(["script", "style", "noscript", "svg", "iframe"]):
                            element.decompose()
                        text = " ".join(soup.get_text(" ", strip=True).split())
                else:
                    return {
                        "status": "failed",
                        "reason": f"Unsupported content type: {content_type or 'unknown'}",
                    }
                return {
                    "status": "ok" if text else "failed",
                    "url": final_url,
                    "title": title,
                    "text": text[:max_chars],
                    "content_type": content_type,
                    "page_number": page_number,
                }
    return {"status": "failed", "reason": "Fetch ended without content"}
