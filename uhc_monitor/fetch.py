"""Fetch a source's PDF from its configured URL and check that it is a real PDF."""
import hashlib
import urllib.error
import urllib.request
from dataclasses import dataclass

import pymupdf

from .sources import FAILED, Source

TIMEOUT_SECONDS = 20
MAX_BYTES = 50 * 1024 * 1024


@dataclass
class FetchResult:
    source_id: str
    status: str                # FAILED, or "fetched" when a valid PDF came back
    data: bytes | None = None
    sha256: str | None = None
    page_count: int | None = None
    reason: str | None = None  # why it failed

    @property
    def ok(self):
        return self.status == "fetched"


def validate_pdf(data):
    """Return (page_count, None) for a real PDF, or (None, reason) otherwise."""
    if not data:
        return None, "empty response"
    if not data.lstrip().startswith(b"%PDF-"):
        return None, "not a PDF (missing %PDF- header; likely an HTML or error page)"
    if b"%%EOF" not in data[-1024:]:
        return None, "PDF is truncated (missing %%EOF trailer)"
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            pages = len(doc)
    except Exception as e:
        return None, f"PDF could not be opened: {e}"
    if pages == 0:
        return None, "PDF has no pages"
    return pages, None


def _fail(source, reason):
    return FetchResult(source.id, FAILED, reason=reason)


def fetch_source(source: Source):
    """Fetch one source. Never raises; any problem comes back as a failed result."""
    if not source.url:
        return _fail(source, "no URL configured")
    try:
        with urllib.request.urlopen(source.url, timeout=TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                return _fail(source, f"HTTP {resp.status}")
            data = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        return _fail(source, f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return _fail(source, f"network error: {getattr(e, 'reason', e)}")
    if len(data) > MAX_BYTES:
        return _fail(source, "response too large")
    pages, problem = validate_pdf(data)
    if problem:
        return _fail(source, problem)
    return FetchResult(source.id, "fetched", data, hashlib.sha256(data).hexdigest(), pages)
