"""Run fetch against every local test-server scenario and print what it reports."""
from . import testserver
from .fetch import fetch_source
from .sources import Source, load_sources


def main():
    server, base = testserver.start()
    cases = [("valid PDF", "/ok.pdf"), ("HTTP 200 HTML error page", "/html-error"),
             ("HTML labelled application/pdf", "/html-as-pdf"), ("HTTP 200 empty body", "/empty"),
             ("truncated PDF", "/truncated.pdf"), ("HTTP 404", "/missing"),
             ("HTTP 500", "/server-error"), ("server unreachable", None)]
    print(f"{'scenario':32} {'result':8} detail")
    for label, path in cases:
        url = f"{base}{path}" if path else "http://127.0.0.1:1/ok.pdf"
        r = fetch_source(Source("beds-mattresses", "Beds and Mattresses", "UnitedHealthcare", url))
        detail = f"{r.page_count} pages, sha256 {r.sha256[:12]}…" if r.ok else r.reason
        print(f"{label:32} {r.status:8} {detail}")
    print("\nConfigured registry (config/sources.json):")
    for s in load_sources():
        r = fetch_source(s)
        print(f"  {s.id}: url={s.url} -> {r.status} ({r.reason})")
    server.shutdown()


if __name__ == "__main__":
    main()
