"""Story "Caller has reached the hospital by mistake" -- an unrelated casual sentence must NEVER disturb an active hospital task.

# ADDED BY SOURAV: the active-flow half of the proof, driven through the real `_dispatch_turn` with the KCD-387 home-collection harness
# (a real flow state machine with held slots and a quote) plus the other in-progress states `_dispatch_turn` already honours (the KCD-382b
# preparation question, the history flow, a pending enquiry question). While any of them is open, an unrelated-sounding turn:
#   * is NOT counted, so repeated casual turns can never accumulate into a close;
#   * does NOT close the call;
#   * leaves the task's state (stage, held slot, collected values) exactly as it was.
# And a real home-collection request wrapped in casual wording still reaches the real flow, even if the model wrongly says "unrelated".

  python -m pytest tests/test_orchestrator_unrelated_caller_flows.py -v
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_orchestrator_home_collection import SLOTS, env, m, reach_proceed, text_of  # noqa: F401  (the KCD-387 harness)

from agent.phrases import phrase

FIRST_EN = phrase("unrelated_first", "en")
FINAL_EN = phrase("unrelated_final", "en")


def _untouched(env, before):
    s = env.session
    assert not s.ws.closed and s.unrelated_count == 0 and s.outcome == "completed"
    assert s.signals.unrelated_caller is False
    assert s.home_collection is before["state"] and s.home_collection.stage == before["stage"]
    assert env.tools.releases == before["releases"]  # a casual remark never releases a held slot


@pytest.mark.asyncio
@pytest.mark.parametrize("model_says", ["unrelated_caller", "smalltalk"])
async def test_a_casual_sentence_during_home_collection_does_not_destroy_the_flow(m, env, model_says):
    await reach_proceed(env)
    st = env.session.home_collection
    assert st is not None and st.stage == "awaiting_proceed"
    before = {"state": st, "stage": st.stage, "releases": list(env.tools.releases), "holds": list(env.tools.holds)}
    names_before, quote_before = list(st.test_names), st.quote
    for _ in range(3):  # three casual turns in a row: still no closing streak
        said = await env.say("Bhai waise kaise ho?", model_says, {})
        assert FINAL_EN not in text_of(said) and FIRST_EN not in text_of(said)
    _untouched(env, before)
    assert st.test_names == names_before and st.quote == quote_before and env.tools.holds == before["holds"]


@pytest.mark.asyncio
async def test_the_flow_still_works_after_the_casual_interruption(m, env):
    await reach_proceed(env)
    await env.say("Bhai waise kaise ho?", "unrelated_caller", {})
    said = await env.say("yes", "unclear", {})  # the caller comes back to the question that was asked
    # exactly what the same "yes" gets on the unmodified code: the flow moves on and asks for the patient's name
    assert "patient's name" in text_of(said) and not env.session.ws.closed


@pytest.mark.asyncio
async def test_a_person_name_phrase_during_home_collection_is_not_read_as_a_wrong_caller(m, env):
    await reach_proceed(env)
    st = env.session.home_collection
    await env.say("Suresh hai kya?", "unclear", {})
    assert not env.session.ws.closed and env.session.unrelated_count == 0 and env.session.home_collection is st


@pytest.mark.asyncio
async def test_a_wrong_number_phrase_during_home_collection_is_left_to_the_flow(m, env):
    await reach_proceed(env)
    said = await env.say("Sorry wrong number", "unclear", {})
    # The KCD-387 proceed-question gate answers this turn exactly as it did before this story (it reads a non-"yes" as a decline);
    # what this test pins is that the UNRELATED path stayed out of it: nothing closed, nothing counted, nothing re-filed.
    s = env.session
    assert (
        not s.ws.closed and s.outcome == "completed" and s.unrelated_count == 0 and s.signals.unrelated_caller is False
    )
    assert FIRST_EN not in text_of(said) and FINAL_EN not in text_of(said)


@pytest.mark.asyncio
async def test_a_casual_home_collection_request_reaches_the_real_flow(m, env):
    said = await env.say(
        "Arre dada kemon acho, amar Uric Acid test ta bari theke collect hobe? pincode 700091",
        "home_collection",
        dict(SLOTS),
    )
    assert env.session.home_collection is not None and env.session.unrelated_count == 0
    assert "Uric Acid" in text_of(said)


@pytest.mark.asyncio
async def test_a_casual_home_collection_request_survives_a_model_that_wrongly_says_unrelated(m, env):
    said = await env.say("Arre dada kemon acho, amar test ta bari theke collect hobe?", "unrelated_caller", {})
    assert said == [phrase("unclear", "en")]  # vetoed to a plain re-ask: never a close
    assert not env.session.ws.closed and env.session.unrelated_count == 0 and env.session.outcome == "completed"


@pytest.mark.asyncio
async def test_unrelated_chat_then_a_home_collection_question_resets_and_runs_the_flow(m, env):
    await env.say("Arre bhai kaise ho?", "unrelated_caller", {})
    assert env.session.unrelated_count == 1 and env.session.outcome == "unrelated_caller"
    await env.say("can this be collected at home, my pincode is 700091", "home_collection", dict(SLOTS))
    assert (
        env.session.unrelated_count == 0
        and env.session.outcome == "completed"
        and env.session.home_collection is not None
    )
    assert not env.session.ws.closed


# ----------------------------------------------------------------------------- the other states _dispatch_turn already honours


@pytest.mark.asyncio
async def test_an_open_preparation_question_is_not_a_wrong_caller(m, env):
    s = env.session
    s.awaiting_test_prep_registration_answer = (
        True  # KCD-382b: "are you a registered patient?" is waiting for its answer
    )
    await env.say("Aur batao kya chal raha hai?", "unrelated_caller", {})
    assert not s.ws.closed and s.unrelated_count == 0 and s.outcome == "completed"
    assert s.signals.unrelated_caller is False


@pytest.mark.asyncio
async def test_an_open_history_question_is_not_a_wrong_caller(m, env):
    s = env.session
    s.history_state = "need_phone"
    said = await env.say("Aur batao kya chal raha hai?", "unrelated_caller", {})
    # (the history flow has its own, older give-up rule after repeated misses -- unchanged; one turn proves the unrelated path stays out)
    assert not s.ws.closed and s.unrelated_count == 0 and s.outcome != "unrelated_caller"
    assert FIRST_EN not in text_of(said) and FINAL_EN not in text_of(said)


@pytest.mark.asyncio
async def test_an_open_enquiry_question_is_not_a_wrong_caller(m, env):
    s = env.session
    s.pending_enquiry = ("test_rate", s.turn_count)  # "which test?" was just asked
    await env.say("Aur batao kya chal raha hai?", "unrelated_caller", {})
    assert not s.ws.closed and s.unrelated_count == 0 and s.outcome == "completed"


@pytest.mark.asyncio
async def test_the_flow_snapshot_ignores_a_finished_task(m, env):
    s = env.session
    assert m._unrelated_flow_active(s) is False
    s.home_collection = None
    await env.say("Arre bhai kaise ho?", "unrelated_caller", {})
    assert s.unrelated_count == 1  # no task open: the unrelated path applies as designed
