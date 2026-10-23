"""The four segments the pilot is scoped to (2026-10-07), as whole conversations in Bengali, Hindi and
English, through the REAL orchestrator, the REAL clinic API and the REAL speech gate
(tests/_conversation.py), on the shipped settings (deploy/env.sh):

  1. doctor -- when a doctor sits, booking (tests/test_conversation_flows.py), cancelling, and
     "what have I booked?"
  2. lab test price -- one test, or several with their total
  3. lab report requests and lab test bookings -- both go to the counter
  4. FAQ -- address, hours, parking, payment

Each test pins what the CALLER hears, against the clinic's own data -- never a figure typed into the
test -- and that every line passed the speech gate (call.blocked == []), because a line the gate
blocks is a line the caller never heard.
"""

from __future__ import annotations

import datetime
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _conversation import conversation  # noqa: E402

from agent.lookup_request import asks_what_is_booked  # noqa: E402
from agent.phrases import phrase  # noqa: E402
from agent.reply_templates_i18n import WEEKDAY_WORDS  # noqa: E402
from agent.report_request import asks_about_report  # noqa: E402
from agent.spoken_date import clinic_today  # noqa: E402

LANGS = ("bn", "hi", "en")
YES = {"bn": "হ্যাঁ", "hi": "हाँ", "en": "yes"}
PHONE = "9830077777"  # a number no seeded patient has, so nothing but this call's booking is on it
PATIENT = {"bn": "রাহুল দাস", "hi": "राहुल दास", "en": "Rahul Das"}


@pytest.fixture(autouse=True)
def _shipped_settings(monkeypatch):
    """deploy/env.sh as shipped: the unreviewed lexicon OFF, a booking lookup verified by number and name."""
    monkeypatch.setenv("PRONUNCIATION_ALLOW_DRAFT", "0")
    monkeypatch.setenv("LOOKUP_VERIFICATION", "name")


def _book_ahead(call, lang) -> dict:
    """A confirmed appointment with Dr. A. Sen on a day he sits, at least a day ahead, made through the
    clinic API inside this call's own database. Returns the booking."""
    tools = call.m._tools
    for ahead in range(1, 15):
        day = (datetime.date.today() + datetime.timedelta(days=ahead)).isoformat()
        avail = call._run(tools.get_doctor_availability("Dr. A. Sen", day))
        if avail.get("available"):
            start = avail["chamber_hours"].split("-")[0]
            r = call._run(
                tools._client.post(
                    "/api/v1/appointments",
                    json={"doctor_name": "Dr. A. Sen", "date": day, "time_slot": start,
                          "patient_name": PATIENT[lang], "phone": PHONE},
                )
            )
            body = r.json()
            assert body.get("success"), body
            return body
    raise AssertionError("Dr. A. Sen has no sitting in the next two weeks")


# =============================================================================== 1. doctor


WHEN_DOES_SEN_SIT = {
    "bn": ("ডাক্তার সেন কবে বসেন?", "সেন"),
    "hi": ("डॉक्टर सेन कब बैठते हैं?", "सेन"),
    "en": ("When does Dr Sen sit?", "Sen"),
}


@pytest.mark.parametrize("lang", LANGS)
def test_when_a_doctor_sits_is_answered_with_their_days_and_next_sitting(lang):
    with conversation(lang) as call:
        avail = call._run(call.m._tools.get_doctor_availability("Dr. A. Sen", None))
        text, said_name = WHEN_DOES_SEN_SIT[lang]
        reply = call.say(text, "doctor_availability", {"doctor_name": said_name})[-1]
        for day in avail["chamber_days"]:
            assert WEEKDAY_WORDS[lang][day] in reply, reply
        assert avail["date"] in reply  # the next sitting, as the clinic gave it
        assert call.blocked == []


def test_a_sitting_already_over_today_is_never_offered_as_the_next_one():
    """At 16:41 a 10:00-12:00 chamber was offered as "today", and every time the caller gave was then
    refused. Checked for every doctor at whatever time this runs: a next sitting dated today must still
    have a bookable start after now."""
    with conversation("en") as call:
        tools = call.m._tools
        doctors = call._run(tools._client.get("/api/v1/catalogue")).json()["doctors"]
        now_hm = datetime.datetime.now().strftime("%H:%M")
        today = datetime.date.today().isoformat()
        for d in doctors:
            avail = call._run(tools.get_doctor_availability(d["name"], None))
            if avail.get("date") == today:
                start, end = avail["chamber_hours"].split("-")
                assert end > now_hm, (d["name"], avail)


FAMILY_BOOKINGS = {
    "bn": ["আমার মায়ের জন্য ডাক্তার সেনের অ্যাপয়েন্টমেন্ট", "আমার অ্যাপয়েন্টমেন্ট লাগবে"],
    "hi": ["मेरी माँ के लिए डॉक्टर सेन का अपॉइंटमेंट", "पापा के लिए बुकिंग करनी है", "क्या बिना बुकिंग के सीधे आ सकते हैं?"],
    "en": ["An appointment with Dr Sen for my mother", "Book an appointment for my father"],
}


@pytest.mark.parametrize("lang", LANGS)
def test_a_new_booking_said_with_my_is_not_taken_for_a_lookup(lang):
    for text in FAMILY_BOOKINGS[lang]:
        assert not asks_what_is_booked(text), text


@pytest.mark.parametrize("lang", LANGS)
def test_a_family_booking_starts_a_booking(lang):
    with conversation(lang) as call:
        text = FAMILY_BOOKINGS[lang][0]
        call.say(text, "book_appointment", {"doctor_name": WHEN_DOES_SEN_SIT[lang][1], "relationship": "mother"})
        st = call.session.booking
        assert st is not None and st.action == "book_appointment" and st.doctor_verified
        assert call.blocked == []


CANCEL = {
    "bn": "আমার অ্যাপয়েন্টমেন্টটা বাতিল করতে চাই",
    "hi": "मुझे अपना अपॉइंटमेंट कैंसिल करना है",
    "en": "I want to cancel my appointment",
}


@pytest.mark.parametrize("lang", LANGS)
def test_cancel_finds_the_booking_by_number_and_cancels_it_on_yes(lang):
    with conversation(lang) as call:
        booking = _book_ahead(call, lang)
        call.say(CANCEL[lang], "cancel_appointment", {})
        # The cancel flow ran -- not the record-verification flow "my appointment" used to start.
        assert call.session.history_state is None
        assert call.session.booking is not None and call.session.booking.action == "cancel_appointment"
        readback = call.say(PHONE, "unclear", {"phone": PHONE})[-1]
        assert readback.endswith("?")
        call.say(YES[lang], "unclear", {})
        if call.session.booking is not None and call.session.booking.stage == "awaiting_charge_confirm":
            # Inside the charging window (story "Caller cancels an appointment"): the charge was stated
            # and confirmed again BEFORE anything was cancelled.
            still = call._run(call.m._tools.lookup_bookings(confirmation_id=booking["confirmation_id"]))
            assert still.get("bookings"), "cancelled before the charge was confirmed"
            call.say(YES[lang], "unclear", {})
        found = call._run(call.m._tools.lookup_bookings(confirmation_id=booking["confirmation_id"]))
        assert found.get("bookings") == []  # cancelled: no longer an upcoming confirmed booking
        assert call.blocked == []


LOOKUP = {"bn": "আমার কী বুক করা আছে?", "hi": "मेरी क्या बुकिंग है?", "en": "What do I have booked?"}


@pytest.mark.parametrize("lang", LANGS)
def test_what_have_i_booked_is_answered_after_number_and_name(lang):
    with conversation(lang) as call:
        booking = _book_ahead(call, lang)
        call.say(LOOKUP[lang], "lookup_booking", {})
        # The bare number answers the question just asked -- not "I did not understand".
        asked_name = call.say(PHONE, "unclear", {"phone": PHONE})[-1]
        assert asked_name.endswith("?")
        reply = call.say(PATIENT[lang], "unclear", {"patient_name": PATIENT[lang]})[-1]
        assert booking["confirmation_id"] in reply and booking["date"] in reply
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_a_name_that_does_not_match_hears_nothing_about_the_booking(lang):
    other = {"bn": "অমিত রায়", "hi": "अमित राय", "en": "Amit Roy"}[lang]
    with conversation(lang) as call:
        booking = _book_ahead(call, lang)
        call.say(LOOKUP[lang], "lookup_booking", {})
        call.say(PHONE, "unclear", {"phone": PHONE})
        reply = call.say(other, "unclear", {"patient_name": other})[-1]
        assert reply.startswith(phrase("lookup_not_matched", lang))
        assert booking["confirmation_id"] not in " ".join(line for who, line in call._log if who.startswith("AI"))
        assert call.blocked == []


MOVE_TO_FRIDAY = {"bn": "ওটা শুক্রবারে সরিয়ে দিন", "hi": "इसे शुक्रवार को कर दीजिए", "en": "Move it to Friday"}
EVENING = {"bn": "বিকেল পাঁচটায়", "hi": "शाम पाँच बजे", "en": "five in the evening"}
ELEVEN = {"bn": "সকাল এগারোটায়", "hi": "सुबह ग्यारह बजे", "en": "eleven in the morning"}


def _rows(sql, *args):
    import sqlite3

    with sqlite3.connect(os.environ["CLINIC_DB_PATH"]) as c:
        return c.execute(sql, args).fetchall()


@pytest.mark.parametrize("lang", LANGS)
def test_a_booking_read_out_can_be_moved_to_another_day_and_is_moved_in_the_database(lang):
    """Fetch -> "move it to Friday" (Dr Sen does not sit Fridays) -> his next sitting is offered and taken ->
    a time outside his chamber is refused with the hours -> a time inside is read back with its weekday ->
    yes. The old appointment is marked rescheduled and its slot freed; the new one is confirmed. The model's
    date for "Friday" is deliberately WRONG here: the calendar's is the one used."""
    with conversation(lang) as call:
        booking = _book_ahead(call, lang)
        call.say(LOOKUP[lang], "lookup_booking", {})
        call.say(PHONE, "unclear", {"phone": PHONE})
        call.say(PATIENT[lang], "unclear", {"patient_name": PATIENT[lang]})
        # no confirmation number is asked for: the booking just read out is the one moved
        offer = call.say(MOVE_TO_FRIDAY[lang], "reschedule_appointment", {"new_date": "2025-01-01"})[-1]
        assert offer.endswith("?")
        friday = next(
            (clinic_today() + datetime.timedelta(days=n)) for n in range(1, 8)
            if (clinic_today() + datetime.timedelta(days=n)).weekday() == 4
        )
        avail = call._run(call.m._tools.get_doctor_availability("Dr. A. Sen", friday.isoformat()))
        assert not avail["available"]  # the seed: Dr Sen sits Tuesday, Thursday, Saturday
        new_day = avail["next_available_date"]
        assert new_day in offer
        call.say(YES[lang], "unclear", {})
        hours = call.say(EVENING[lang], "unclear", {"time_slot": "17:00"})[-1]
        assert avail["chamber_hours"] is None and "10:00-12:00" in hours
        readback = call.say(ELEVEN[lang], "unclear", {"time_slot": "11:00"})[-1]
        assert new_day in readback and WEEKDAY_WORDS[lang][datetime.date.fromisoformat(new_day).weekday()] in readback
        call.say(YES[lang], "unclear", {})
        assert _rows("select status from appointments where confirmation_id=?", booking["confirmation_id"]) == [
            ("rescheduled",)
        ]
        assert _rows(
            "select status from appointments where phone=? and date=? and time_slot='11:00'", PHONE, new_day
        ) == [("confirmed",)]
        assert _rows("select * from slot_locks where date=? and time_slot=?", booking["date"], booking["time_slot"]) == []
        assert call.blocked == []


NEXT_WORD = {"bn": "আগামী", "hi": "अगले", "en": "next"}


@pytest.mark.parametrize("lang", LANGS)
def test_a_booking_day_said_in_words_is_the_calendar_date_not_the_models(lang):
    """"next <weekday>" for Dr Sen's next sitting, with the model's own date deliberately wrong: the day
    the booking holds, and reads back with its weekday, is the calendar's."""
    with conversation(lang) as call:
        today = clinic_today()
        sitting = next(
            today + datetime.timedelta(days=n) for n in range(1, 8)
            if call._run(
                call.m._tools.get_doctor_availability("Dr. A. Sen", (today + datetime.timedelta(days=n)).isoformat())
            ).get("available")
        )
        weekday = WEEKDAY_WORDS[lang][sitting.weekday()]
        call.say(WHEN_DOES_SEN_SIT[lang][1], "book_appointment", {"doctor_name": WHEN_DOES_SEN_SIT[lang][1]})
        call.say(f"{NEXT_WORD[lang]} {weekday}", "book_appointment", {"date": "2025-01-01", "time_slot": "10:00"})
        assert call.session.booking.slots.get("date") == sitting.isoformat()
        text = f"{PATIENT[lang]} {PHONE}"
        readback = call.say(text, "book_appointment", {"patient_name": PATIENT[lang], "phone": PHONE})[-1]
        assert sitting.isoformat() in readback and weekday in readback
        assert call.blocked == []


# =============================================================================== 2. lab test price


SEVERAL = {
    "bn": ("সিবিসি, লিপিড প্রোফাইল আর টিএসএইচ এর দাম কত?", ["সিবিসি", "লিপিড প্রোফাইল", "টিএসএইচ"]),
    "hi": ("सीबीसी, लिपिड प्रोफाइल और टीएसएच का रेट क्या है?", ["सीबीसी", "लिपिड प्रोफाइल", "टीएसएच"]),
    "en": ("What do CBC, lipid profile and TSH cost?", ["CBC", "lipid profile", "TSH"]),
}


@pytest.mark.parametrize("lang", LANGS)
def test_several_tests_are_each_priced_and_totalled_from_the_catalogue(lang):
    with conversation(lang) as call:
        text, names = SEVERAL[lang]
        rates = [call._run(call.m._tools.get_test_rate(n))["rate_inr"] for n in names]
        reply = call.say(text, "test_rate", {"test_names": names})[-1]
        for rate in rates:
            assert str(int(rate)) in reply
        assert str(int(sum(rates))) in reply  # the total, worked out in code from the catalogue figures
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_the_same_test_named_twice_is_priced_once(lang):
    names = {"bn": ["সিবিসি", "সি বি সি"], "hi": ["सीबीसी", "सी बी सी"], "en": ["CBC", "complete blood count"]}[lang]
    with conversation(lang) as call:
        rate = int(call._run(call.m._tools.get_test_rate(names[0]))["rate_inr"])
        reply = call.say(" ".join(names), "test_rate", {"test_names": names})[-1]
        assert str(rate * 2) not in reply
        assert call.blocked == []


PARTLY_UNKNOWN = {
    "bn": ["সিবিসি", "ইএসআর", "ফুল বডি চেকআপ"],
    "hi": ["सीबीसी", "ईएसआर", "फुल बॉडी चेकअप"],
    "en": ["CBC", "ESR", "full body checkup"],
}
FOUND_TOTAL = {
    "bn": "যে টেস্টগুলো পেলাম, সেগুলো মিলিয়ে মোট",
    "hi": "जो टेस्ट मिले, उनका कुल",
    "en": "The ones I found come to",
}


@pytest.mark.parametrize("lang", LANGS)
def test_a_total_with_an_unknown_test_is_labelled_as_the_found_ones_only(lang):
    with conversation(lang) as call:
        names = PARTLY_UNKNOWN[lang]
        reply = call.say(" ".join(names), "test_rate", {"test_names": names})[-1]
        assert FOUND_TOTAL[lang] in reply
        assert names[2] in reply  # the unknown one is named back, on the not-found path
        # and the price question stays open for the caller's next bare name
        assert call.session.pending_enquiry and call.session.pending_enquiry[0] == "test_rate"
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_a_name_matching_two_tests_is_asked_about_never_priced(lang):
    names = {"bn": ["সিবিসি", "সুগার"], "hi": ["सीबीसी", "शुगर"], "en": ["CBC", "sugar"]}[lang]
    with conversation(lang) as call:
        fasting = int(call._run(call.m._tools.get_test_rate("Blood Sugar Fasting"))["rate_inr"])
        cbc = int(call._run(call.m._tools.get_test_rate(names[0]))["rate_inr"])
        reply = call.say(" ".join(names), "test_rate", {"test_names": names})[-1]
        assert reply.endswith("?")
        assert str(cbc + fasting) not in reply  # no guessed total
        assert call.blocked == []


# =============================================================================== 3. reports and lab bookings


REPORT_REQUESTS = {
    "bn": ["আমার রিপোর্ট কি তৈরি হয়েছে?", "আমার রিপোর্টটা পাঠিয়ে দিন", "রিপোর্ট কোথায় নিতে আসব?"],
    "hi": ["क्या मेरी रिपोर्ट तैयार है?", "मेरी रिपोर्ट भेज दीजिए", "रिपोर्ट कहाँ से लेनी है?"],
    "en": ["Is my report ready?", "Please send me my report", "Where do I collect the report?"],
}


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("label", [("unclear", {}), ("clinic_faq", {"faq_topic": "report_collection"})])
def test_a_report_request_is_sent_to_the_counter_whatever_the_model_called_it(lang, label):
    for text in REPORT_REQUESTS[lang]:
        with conversation(lang) as call:
            reply = call.say(text, *label)[-1]
            assert reply == phrase("report_counter_only", lang), (text, reply)
            assert call.blocked == []


TURNAROUND = {
    "bn": ("সিবিসি রিপোর্ট কতক্ষণে পাব?", "সিবিসি"),
    "hi": ("सीबीसी रिपोर्ट कितनी देर में मिलेगी?", "सीबीसी"),
    "en": ("How long does the CBC report take?", "CBC"),
}


@pytest.mark.parametrize("lang", LANGS)
def test_how_long_a_tests_report_takes_is_not_sent_to_the_counter(lang):
    text, name = TURNAROUND[lang]
    assert not asks_about_report(text)
    with conversation(lang) as call:
        hours = call._run(call.m._tools.get_test_rate(name))["report_time_hours"]
        reply = call.say(text, "test_rate", {"test_name": name})[-1]
        assert str(int(hours)) in reply
        assert call.blocked == []


IS_IT_BAD = {"bn": "আমার রিপোর্টটা কি খুব খারাপ?", "hi": "क्या मेरी रिपोर्ट खतरनाक है?", "en": "Is my result dangerous?"}


@pytest.mark.parametrize("lang", LANGS)
def test_whether_a_report_is_bad_still_gets_the_clinical_reply(lang):
    with conversation(lang) as call:
        reply = call.say(IS_IT_BAD[lang], "unclear", {})[-1]
        assert reply != phrase("report_counter_only", lang)
        assert phrase("clinical_interpretation", lang) in reply
        assert call.blocked == []


@pytest.mark.parametrize("lang", LANGS)
def test_a_report_question_mid_booking_is_answered_and_the_booking_resumed(lang):
    text, name = WHEN_DOES_SEN_SIT[lang]
    with conversation(lang) as call:
        call.say(text, "book_appointment", {"doctor_name": name})
        reply = call.say(REPORT_REQUESTS[lang][0], "unclear", {})[-1]
        assert reply.startswith(phrase("report_counter_only", lang))
        assert reply.endswith("?")  # the booking's next question follows
        assert call.session.booking is not None and call.session.booking.doctor_verified
        assert call.blocked == []


BOOK_A_TEST = {
    "bn": ("আমি সিবিসি টেস্ট বুক করতে চাই", ["সিবিসি"]),
    "hi": ("मुझे सीबीसी टेस्ट बुक करना है", ["सीबीसी"]),
    "en": ("I want to book a CBC test", ["CBC"]),
}


@pytest.mark.parametrize("lang", LANGS)
def test_a_lab_test_booking_is_sent_to_the_counter(lang):
    text, names = BOOK_A_TEST[lang]
    with conversation(lang) as call:
        reply = call.say(text, "book_test", {"test_names": names})[-1]
        assert reply == phrase("lab_test_counter_only", lang)
        assert call.session.booking is None
        assert call.blocked == []


# =============================================================================== 4. FAQ


FAQ = {
    "location": {"bn": "আপনাদের ঠিকানা কী?", "hi": "आपका पता क्या है?", "en": "What is your address?"},
    "hours": {"bn": "আপনারা কখন খোলা থাকেন?", "hi": "आप कब खुले रहते हैं?", "en": "What time do you open?"},
    "parking": {"bn": "পার্কিং আছে?", "hi": "पार्किंग है?", "en": "Is there parking?"},
    "payment_methods": {"bn": "কার্ডে পেমেন্ট করা যায়?", "hi": "कार्ड से पेमेंट होता है?", "en": "Can I pay by card?"},
}


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("topic", sorted(FAQ))
def test_a_faq_is_answered_with_the_clinics_own_answer(lang, topic):
    with conversation(lang) as call:
        answer = call._run(call.m._tools.get_faq(topic, lang))
        reply = call.say(FAQ[topic][lang], "clinic_faq", {"faq_topic": topic})[-1]
        expected = answer.get("answer") or answer.get(f"answer_{lang}")
        assert expected and expected in reply, (answer, reply)
        assert call.blocked == []
