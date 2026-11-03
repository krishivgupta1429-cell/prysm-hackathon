"""Built during the Meta Global AI Developer Hackathon (new work).

Test guard: the offline tests must never reach the network. The live-trends call is replaced by one that raises (the
trends stage then fails soft to "unavailable", as it would offline) and the in-memory trends cache starts empty.
Tests of the trends stage itself pass their own fake call.
"""
import pytest


@pytest.fixture(autouse=True)
def no_network_for_trends(monkeypatch):
    from app.studio import trends

    def blocked(api_key, prompt, timeout_s):
        raise RuntimeError("network access is disabled in the offline tests")

    monkeypatch.setattr(trends, "_call_gemini", blocked)
    trends._memory.clear()
