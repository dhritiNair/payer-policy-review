"""Shared fixtures. Every test runs against throwaway state and never calls the real Claude API."""
from pathlib import Path

import pytest

from uhc_monitor.extract import extract_policy

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Fresh store and Claude call log per test; an empty API key so nothing can reach the network."""
    monkeypatch.setenv("UHC_STORE_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("UHC_LLM_LOG", str(tmp_path / "llm_calls.jsonl"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    return tmp_path / "store"


@pytest.fixture
def app_env(isolated_env):
    """The store root the Streamlit page will use (a string)."""
    return str(isolated_env)


@pytest.fixture(scope="session")
def real_doc():
    return extract_policy(ROOT / "sources" / "original" / "beds-mattresses.pdf")


@pytest.fixture(scope="session")
def real_pdf_bytes():
    return (ROOT / "sources" / "original" / "beds-mattresses.pdf").read_bytes()
