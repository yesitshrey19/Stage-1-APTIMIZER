"""Step 7: the SSE chat route, exercised with a stubbed provider.

No AI key is configured here, so the provider itself cannot be tested. Everything between
the provider and the browser can be, and that is where the bugs would be: frame shape, the
done event, whether the citation guard actually runs on the assembled text, whether the
thread is stored, and what a mid-stream failure does.

The rule this file holds: tokens reach the reader unverified -- that is what streaming
means -- but the STORED message and the flags the UI renders must have been through
citations.verify(). A fast answer carrying an invented IS clause is worse than a slow
correct one.

Uses the session-scoped client from tests/conftest.py; see the note there on why there can
only be one.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import server, ai as ailib, aptspeed


# Function-scoped, because api_project is: each test gets a clean project and
# they cannot leak state into one another.
@pytest.fixture
def client_and_project(api_project):
    return api_project


def frames(text):
    """Parse an SSE body into the JSON objects it carried."""
    out = []
    for frame in text.split("\n\n"):
        line = next((l for l in frame.split("\n") if l.startswith("data:")), None)
        if line:
            out.append(json.loads(line[5:].strip()))
    return out


def stub(chunks, model="stub-model", provider="stub"):
    """A stand-in for ailib.stream_markdown: yields `chunks`, then the done tuple."""
    async def _gen(system, prompt, *, session_hint="", prefer_fast=False):
        for c in chunks:
            yield c
        yield ("__done__", model, provider)
    return _gen


def post(client, pid, text):
    return client.post(f"/api/projects/{pid}/ai/chat/stream",
                       json={"messages": [{"role": "user", "content": text}]})


# ---------------------------------------------------------------- the no-model path
def test_a_greeting_streams_without_touching_a_provider(client_and_project, monkeypatch):
    client, pid = client_and_project

    def explode(*a, **k):
        raise AssertionError("a greeting must never reach the provider")
    monkeypatch.setattr(ailib, "stream_markdown", explode)

    r = post(client, pid, "hi")
    assert r.status_code == 200
    evts = frames(r.text)
    assert evts[0]["delta"] == aptspeed.GREETING
    assert "done" in evts[-1] and evts[-1]["done"]["model"] == "local"
    client.delete(f"/api/projects/{pid}/ai/chat")


def test_a_capability_question_answers_from_the_fixed_text(client_and_project, monkeypatch):
    client, pid = client_and_project

    def explode(*a, **k):
        raise AssertionError("no provider for a capability question")
    monkeypatch.setattr(ailib, "stream_markdown", explode)

    evts = frames(post(client, pid, "what can you do").text)
    assert "Explain a number" in evts[0]["delta"]
    client.delete(f"/api/projects/{pid}/ai/chat")


# ---------------------------------------------------------------- streaming proper
def test_deltas_arrive_in_order_and_reassemble(client_and_project, monkeypatch):
    client, pid = client_and_project
    monkeypatch.setattr(ailib, "stream_markdown",
                        stub(["The base ", "shear is ", "1,240 kN."]))
    evts = frames(post(client, pid, "why is the base shear what it is").text)
    deltas = [e["delta"] for e in evts if "delta" in e]
    assert deltas == ["The base ", "shear is ", "1,240 kN."]
    assert "".join(deltas) == evts[-1]["done"]["content"]
    client.delete(f"/api/projects/{pid}/ai/chat")


def test_the_done_event_carries_the_model_that_answered(client_and_project, monkeypatch):
    client, pid = client_and_project
    monkeypatch.setattr(ailib, "stream_markdown",
                        stub(["ok"], model="fast-1", provider="groq"))
    done = frames(post(client, pid, "explain the foundation clause").text)[-1]["done"]
    assert done["model"] == "fast-1" and done["provider"] == "groq"
    client.delete(f"/api/projects/{pid}/ai/chat")


def test_the_thread_is_stored_and_readable_afterwards(client_and_project, monkeypatch):
    client, pid = client_and_project
    monkeypatch.setattr(ailib, "stream_markdown", stub(["stored answer"]))
    post(client, pid, "why is the cost per flat what it is")
    thread = client.get(f"/api/projects/{pid}/ai/chat").json()["thread"]
    assert thread[-1]["content"] == "stored answer"
    client.delete(f"/api/projects/{pid}/ai/chat")


# ---------------------------------------------------------------- the standing rule
def test_the_citation_guard_runs_on_the_streamed_answer(client_and_project, monkeypatch):
    """The whole point. Tokens go out unverified; the stored message must be checked."""
    client, pid = client_and_project
    monkeypatch.setattr(ailib, "stream_markdown",
                        stub(["Per ", "IS 9999:2099 Cl. 3.2.1", " you must do this."]))
    done = frames(post(client, pid, "which clause governs this").text)[-1]["done"]
    assert done["unverified_citations"] == ["IS 9999:2099 Cl. 3.2.1"]
    client.delete(f"/api/projects/{pid}/ai/chat")


def test_a_real_citation_is_not_flagged_on_a_stream(client_and_project, monkeypatch):
    client, pid = client_and_project
    monkeypatch.setattr(ailib, "stream_markdown",
                        stub(["Base shear per IS 1893:2016 Cl. 7.6.2."]))
    done = frames(post(client, pid, "which clause governs base shear").text)[-1]["done"]
    assert done["verified_citations"] and not done["unverified_citations"]
    client.delete(f"/api/projects/{pid}/ai/chat")


def test_the_guard_never_edits_the_answer(client_and_project, monkeypatch):
    """A clean reply with a hole in it is worse than a flagged one."""
    client, pid = client_and_project
    text = "Per IS 9999:2099 Cl. 1.1 this applies."
    monkeypatch.setattr(ailib, "stream_markdown", stub([text]))
    done = frames(post(client, pid, "which clause applies").text)[-1]["done"]
    assert done["content"] == text
    client.delete(f"/api/projects/{pid}/ai/chat")


# ---------------------------------------------------------------- failure paths
def test_a_stream_that_dies_before_any_token_falls_back(client_and_project, monkeypatch):
    """Nothing has been shown, so the ordinary route can still answer."""
    client, pid = client_and_project

    async def dies(*a, **k):
        raise RuntimeError("provider refused")
        yield  # pragma: no cover -- present so this is an async generator
    monkeypatch.setattr(ailib, "stream_markdown", dies)

    async def ok(*a, **k):
        return {"text": "fallback answer", "model": "m", "provider": "p"}
    monkeypatch.setattr(ailib, "generate_markdown", ok)

    evts = frames(post(client, pid, "why is the cost what it is").text)
    assert any(e.get("delta") == "fallback answer" for e in evts)
    assert evts[-1]["done"]["content"] == "fallback answer"
    client.delete(f"/api/projects/{pid}/ai/chat")


def test_a_stream_that_dies_mid_answer_reports_rather_than_restarting(client_and_project,
                                                                     monkeypatch):
    """Restarting would rewrite text already on the reader's screen."""
    client, pid = client_and_project

    async def half(*a, **k):
        yield "the first half "
        raise RuntimeError("connection lost")
    monkeypatch.setattr(ailib, "stream_markdown", half)

    called = {"n": 0}

    async def should_not_run(*a, **k):
        called["n"] += 1
        return {"text": "x", "model": "m", "provider": "p"}
    monkeypatch.setattr(ailib, "generate_markdown", should_not_run)

    evts = frames(post(client, pid, "why is the base shear what it is").text)
    assert evts[0]["delta"] == "the first half "
    assert "error" in evts[-1]
    assert called["n"] == 0, "it restarted a stream that had already shown text"


def test_an_empty_message_list_is_rejected(client_and_project):
    client, pid = client_and_project
    assert client.post(f"/api/projects/{pid}/ai/chat/stream",
                       json={"messages": []}).status_code == 400


def test_the_response_is_declared_as_an_event_stream(client_and_project, monkeypatch):
    client, pid = client_and_project
    monkeypatch.setattr(ailib, "stream_markdown", stub(["x"]))
    r = post(client, pid, "why is this what it is")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers.get("cache-control") == "no-cache"
    client.delete(f"/api/projects/{pid}/ai/chat")


# ---------------------------------------------------------------- tiering on the stream
def test_light_questions_ask_for_the_fast_model(client_and_project, monkeypatch):
    """A derivation must never be routed to the weaker model -- that is precisely where a
    plausible but invented clause number comes from."""
    seen = {}

    async def capture(system, prompt, *, session_hint="", prefer_fast=False):
        seen["prefer_fast"] = prefer_fast
        seen["chars"] = len(prompt)
        yield "ok"
        yield ("__done__", "m", "p")

    client, pid = client_and_project
    monkeypatch.setattr(ailib, "stream_markdown", capture)

    post(client, pid, "tell me about the towers")
    assert seen["prefer_fast"] is True
    light_chars = seen["chars"]

    post(client, pid, "show me the base shear derivation per clause")
    assert seen["prefer_fast"] is False
    assert seen["chars"] > light_chars * 5
    client.delete(f"/api/projects/{pid}/ai/chat")
