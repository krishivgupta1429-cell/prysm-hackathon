"""Built during the Meta Global AI Developer Hackathon (new work).

Cut-off text: the saree-reel result ended a Trend Alignment step mid-word ("... from 'd") because Spark's hidden
reasoning used 9.6k of the 13k-token budget and the JSON was cut off and then repaired by json_repair.
"""
import asyncio
import json

from app.studio import meta_llm_shim as shim
from app.studio import quality
from app.studio.json_tolerant import extract_json_object

CUT = "Step 1 — In your editing app, mark your dance section from 'd"


def fake(text, finish, tin=100, tout=200, reasoning=0, cost=0.01):
    return shim.Message(content=[shim.TextBlock(text)], usage=shim.Usage(tin, tout), model="muse-spark-1.3", stop_reason=finish,
                        cost_usd=cost, reasoning_tokens=reasoning)


def test_a_reply_cut_off_mid_sentence_is_retried_with_more_room_and_the_waste_stays_in_the_cost(monkeypatch):
    monkeypatch.delenv("META_REASONING_HEADROOM", raising=False)
    monkeypatch.delenv("META_SCORING_HEADROOM", raising=False)
    client = shim.AsyncAnthropic()
    budgets = []

    def post(body):
        budgets.append(body["max_tokens"])
        return fake('{"a": "mark your dance section from \'d', "max_tokens", 100, 13000, 9600, 0.07) if len(budgets) == 1 \
            else fake('{"a": "complete."}', "end_turn", 100, 9000, 5000, 0.05)

    monkeypatch.setattr(client, "_post", post)
    reply = asyncio.run(client.messages.create(model="claude-sonnet-4-6", max_tokens=7000, messages=[{"role": "user", "content": "x"}]))
    assert reply.content[0].text == '{"a": "complete."}'
    assert budgets[1] > budgets[0]
    assert reply.usage.output_tokens == 13000 + 9000 and reply.usage.input_tokens == 200      # both attempts are billed
    assert reply.cost_usd == 0.12 and reply.reasoning_tokens == 14600


def test_a_reply_that_keeps_being_cut_off_is_returned_after_the_last_attempt(monkeypatch):
    client = shim.AsyncAnthropic()
    calls = []
    monkeypatch.setattr(client, "_post", lambda body: calls.append(1) or fake("cut", "max_tokens"))
    reply = asyncio.run(client.messages.create(model="x", max_tokens=100, messages=[{"role": "user", "content": "x"}]))
    assert len(calls) == shim.MAX_ATTEMPTS and reply.content[0].text == "cut"


def test_a_complete_reply_is_not_retried(monkeypatch):
    client = shim.AsyncAnthropic()
    calls = []
    monkeypatch.setattr(client, "_post", lambda body: calls.append(1) or fake("done.", "end_turn"))
    asyncio.run(client.messages.create(model="x", max_tokens=100, messages=[{"role": "user", "content": "x"}]))
    assert len(calls) == 1


def test_the_real_cut_off_step_is_detected():
    assert quality.looks_truncated(CUT)
    result = {"parameters": [{"name": "Trend Alignment", "howToDoIt": CUT, "whatToExpect": ""}]}
    found = quality.find_truncated(result)
    assert ("Trend Alignment", "howToDoIt", CUT[-40:]) in found and ("Trend Alignment", "whatToExpect", "") in found


def test_complete_text_with_apostrophes_curly_quotes_and_quoted_phrases_is_not_flagged():
    for t in ["Step 1 — Add text that reads 'don't scroll yet' at 3s. Step 2 — Say “you’re not ready for this” on the beat.",
              "She said ‘it’s fine’ and left.",
              "Add the line “wait for the reveal”",
              "Open with 'POV: it's day 1' ✨",
              "That’s the whole fix.",
              "Use the sound (the original one)."]:
        assert not quality.looks_truncated(t), t
    assert quality.looks_truncated("Add the line “wait for the") and quality.looks_truncated("Use the sound (the original")


def test_the_baseline_validator_keeps_steps_with_apostrophes_curly_quotes_and_quoted_phrases_whole():
    from app.services.prysm_analyse_service import _validate_suggestions
    how = ("Step 1 — At 3s, add a text overlay that reads 'don't scroll yet' so strangers know what's coming. "
           "Step 2 — Say “you’re not ready for this” right on the beat at 5s, then mark your dance section from 'drop' to 'last step'. "
           "Step 3 — Keep the creator’s line 'it’s giving main character' on screen for 2 seconds at 8s and don’t add a second overlay.")
    result = {"parameters": [{"name": "Trend Alignment", "howToDoIt": how}]}
    out = _validate_suggestions(result, {"transcript": "", "segments": []}, {"frames": []}, "my_video")
    text = out["parameters"][0]["howToDoIt"]
    for piece in ["'don't scroll yet'", "what's coming.", "“you’re not ready for this”", "'drop' to 'last step'", "'it’s giving main character'", "don’t add a second overlay."]:
        assert piece in text, piece
    assert not quality.looks_truncated(text)


def test_json_extraction_survives_curly_quotes_and_apostrophes_in_values():
    raw = 'Here you go:\n```json\n{"howToDoIt": "Step 1 — Say ‘it’s fine’ then \\"don\'t stop\\" at 3s."}\n```'
    assert json.loads(extract_json_object(raw))["howToDoIt"].startswith("Step 1 — Say ‘it’s fine’")


def test_closing_phrase_and_step_helpers():
    r = {"parameters": [
        {"name": "Hook & Opening", "howToDoIt": "Step 1 — Do a. Step 2 — Do b. Step 3 — Preview it before you post to make sure it works."},
        {"name": "Shareability", "howToDoIt": "Step 1 — Do c. Step 2 — Preview it before you post to make sure it works."},
        {"name": "Audio & Music", "howToDoIt": "Step 1 — Mix the track. Step 2 — Listen once with headphones."}]}
    assert quality.repeated_closings(r) == 2
    assert quality.steps_per_scored(r) == {"Hook & Opening": 3, "Shareability": 2, "Audio & Music": 2}
