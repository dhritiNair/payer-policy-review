"""Source registry: the known policy documents we are allowed to fetch."""
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "sources.json"

# Per-source outcome of an update check. Fetching can only produce "failed";
# the others are decided once a fetched PDF is compared with the stored snapshot.
UNCHANGED, UPDATED, INITIALIZED, FAILED = "unchanged", "updated", "initialized", "failed"
STATUSES = (UNCHANGED, UPDATED, INITIALIZED, FAILED)


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    payer: str
    url: str | None


def load_sources(path=DEFAULT_CONFIG):
    with open(path) as f:
        return [Source(s["id"], s["name"], s["payer"], s.get("url")) for s in json.load(f)["sources"]]
