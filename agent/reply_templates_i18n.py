"""Hindi and English reply templates.

Same discipline as reply_templates.py (which owns Bengali and dispatches
here): every fact in a spoken sentence is a substitution from a live API
response, never model text. Only the sentence around the fact changes with
the language.

Spoken-name rule, same as the Bengali `_spoken_test_name`: the voice for a
language cannot pronounce another language's script, so the name spoken is
the one in the caller's own script -- Devanagari for Hindi (from the API's
seeded `*_hi` alias), the catalogue's English label for English -- and
never a name in a script the TTS would silently drop.
"""

from __future__ import annotations

import datetime
import re

from agent.home_collection_flow import MAX_SLOTS_READ_ALOUD
from agent.sample_wording import sample_sentence

# The day of the week, per language. Shared by every reply that names a date the agent WORKED OUT
# rather than the caller stated -- the earliest-slot offer, an alternative on another day, and the
# "আগামীকাল মানে মঙ্গলবার" check. Monday first, matching datetime.date.weekday().
WEEKDAY_WORDS: dict[str, tuple[str, ...]] = {
    "bn": ("সোমবার", "মঙ্গলবার", "বুধবার", "বৃহস্পতিবার", "শুক্রবার", "শনিবার", "রবিবার"),
    "hi": ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}


# story: "Caller says tomorrow, day after, or next Monday"
# story: "Caller asks for the earliest available appointment"
def weekday_word(date_iso: str | None, lang: str = "bn") -> str:
    """"মঙ্গলবার" for "2026-09-22". Worked out from the date the API returned -- no clock involved,
    so it cannot drift from the date it labels. An unparseable date names no day rather than
    guessing one."""
    try:
        index = datetime.date.fromisoformat(date_iso).weekday()
    except (TypeError, ValueError):
        return ""
    return WEEKDAY_WORDS.get(lang, WEEKDAY_WORDS["bn"])[index]


def spoken_day(date_iso: str | None, lang: str = "bn") -> str:
    """A date with its weekday in front -- "মঙ্গলবার, 2026-09-22". The ISO date is left as data for
    speech_norm.verbalize() to speak; only the day name is added here."""
    day = weekday_word(date_iso, lang)
    return f"{day}, {date_iso}" if day else str(date_iso or "")


# story: "Caller asks what they have booked"
# Several names read as one clause, with the last joined by the language's own "and" -- a test
# booking can hold three tests under one confirmation number, and "A, B comma C" read flat is a
# list being recited rather than a sentence.
_AND_WORD = {"bn": " আর ", "hi": " और ", "en": " and "}


def spoken_list(names: list[str], lang: str = "bn") -> str:
    items = [n for n in names if n]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + _AND_WORD.get(lang, _AND_WORD["bn"]) + items[-1]

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")

_HI_MISSING = {
    ("test_rate", "test_name"): "किस टेस्ट की कीमत जाननी है, ज़रा बताइए?",
    ("doctor_availability", "doctor_name"): "किन डॉक्टर के बारे में पूछ रहे हैं?",
    ("book_appointment", "doctor_name"): "किस डॉक्टर के साथ अपॉइंटमेंट लेना चाहते हैं?",
    ("book_appointment", "date"): "किस दिन के लिए अपॉइंटमेंट चाहिए?",
    ("book_appointment", "time_slot"): "किस समय के लिए अपॉइंटमेंट चाहिए?",
    ("book_appointment", "patient_name"): "मरीज़ का नाम बताइए?",
    ("book_appointment", "phone"): "एक फ़ोन नंबर दीजिए, ताकि मैं कन्फ़र्मेशन भेज सकूँ?",
    ("test_prep", "test_name"): "किस टेस्ट की तैयारी जाननी है, ज़रा बताइए?",
    ("clinic_faq", "faq_topic"): "माफ़ कीजिए, ठीक से समझ नहीं पाई। क्या आप फिर से बताएँगे?",
    # See the Bengali entry in agent/reply_templates.py for why this exists.
    ("department_query", "symptom_description"): "क्या तकलीफ़ हो रही है, ज़रा बताइए? उसी के हिसाब से बता दूँगी किस विभाग में दिखाना है।",
    ("book_test", "test_names"): "कौन से टेस्ट बुक करना चाहते हैं, ज़रा बताइए?",
    ("book_test", "date"): "किस दिन टेस्ट करवाना चाहते हैं?",
    ("book_test", "patient_name"): "मरीज़ का नाम बताइए?",
    ("book_test", "phone"): "एक फ़ोन नंबर दीजिए, ताकि मैं कन्फ़र्मेशन भेज सकूँ?",
    ("reschedule_appointment", "confirmation_id"): "आपका कन्फ़र्मेशन नंबर या जिस नंबर से बुक किया था वह बताइए?",
    ("reschedule_appointment", "new_date"): "किस दिन ले जाना चाहते हैं?",
    ("reschedule_appointment", "new_time_slot"): "किस समय ले जाना चाहते हैं?",
    ("cancel_appointment", "confirmation_id"): "आपका कन्फ़र्मेशन नंबर या जिस नंबर से बुक किया था वह बताइए?",
    ("add_test_booking", "confirmation_id"): "आपका कन्फ़र्मेशन नंबर बताइए?",
    ("add_test_booking", "test_name"): "कौन सा टेस्ट जोड़ना चाहते हैं?",
    ("lookup_booking", "phone"): "जिस नंबर से बुक किया था वह बताइए?",
    ("lookup_booking", "patient_name"): "बुकिंग किसके नाम से हुई थी? मरीज़ का नाम बताइए?",
    # ADDED BY SOURAV: KCD-387 full flow -- see reply_templates.py's own Bengali entries.
    ("home_collection", "test_name"): "किस टेस्ट के लिए घर पर सैंपल देने की बात पूछ रहे हैं, ज़रा बताइए?",
    ("home_collection", "postal_code"): "आपके इलाके का 6 अंकों का पिनकोड बताइए?",
    ("home_collection", "date"): "किस दिन घर पर सैंपल लेने आना होगा?",
    ("home_collection", "address_line"): "पूरा पता बताइए -- घर नंबर, सड़क, इलाका, शहर सहित?",
    ("home_collection", "patient_name"): "मरीज़ का नाम बताइए?",
    ("home_collection", "phone"): "एक फ़ोन नंबर दीजिए, ताकि बुकिंग की जानकारी भेज सकूँ?",
}
_HI_MISSING_DEFAULT = "माफ़ कीजिए, क्या आप थोड़ा साफ़ बोलेंगे?"

_EN_MISSING = {
    ("test_rate", "test_name"): "Which test would you like the price of?",
    ("doctor_availability", "doctor_name"): "Which doctor are you asking about?",
    ("book_appointment", "doctor_name"): "Which doctor would you like to book with?",
    ("book_appointment", "date"): "Which day would you like the appointment for?",
    ("book_appointment", "time_slot"): "What time would you like the appointment?",
    ("book_appointment", "patient_name"): "May I have the patient's name?",
    ("book_appointment", "phone"): "Could you give me a phone number so I can send the confirmation?",
    ("test_prep", "test_name"): "Which test would you like the preparation for?",
    ("clinic_faq", "faq_topic"): "Sorry, I didn't quite get that. Could you say it again?",
    # See the Bengali entry in agent/reply_templates.py for why this exists.
    ("department_query", "symptom_description"): "Could you tell me what the problem is? I'll tell you which department to see.",
    ("book_test", "test_names"): "Which tests would you like to book?",
    ("book_test", "date"): "Which day would you like the tests for?",
    ("book_test", "patient_name"): "May I have the patient's name?",
    ("book_test", "phone"): "Could you give me a phone number so I can send the confirmation?",
    (
        "reschedule_appointment",
        "confirmation_id",
    ): "Could you give me your confirmation number, or the number you booked from?",
    ("reschedule_appointment", "new_date"): "Which day would you like to move it to?",
    ("reschedule_appointment", "new_time_slot"): "Which time would you like to move it to?",
    (
        "cancel_appointment",
        "confirmation_id",
    ): "Could you give me your confirmation number, or the number you booked from?",
    ("add_test_booking", "confirmation_id"): "Could you give me your confirmation number?",
    ("add_test_booking", "test_name"): "Which test would you like to add?",
    ("lookup_booking", "phone"): "Could you give me the number you booked from?",
    ("lookup_booking", "patient_name"): "Whose name is the booking under? Could you tell me the patient's name?",
    # ADDED BY SOURAV: KCD-387 full flow -- see reply_templates.py's own Bengali entries.
    ("home_collection", "test_name"): "Which test are you asking about for home collection?",
    ("home_collection", "postal_code"): "Could you give me your area's 6-digit pincode?",
    ("home_collection", "date"): "Which day would you like the home visit for?",
    ("home_collection", "address_line"): "Could you give me the full address -- house number, street, area and city?",
    ("home_collection", "patient_name"): "May I have the patient's name?",
    ("home_collection", "phone"): "Could you give me a phone number so I can send the booking details?",
}
_EN_MISSING_DEFAULT = "Sorry, could you say that a little more clearly?"

# Sample types that are a specimen a person gives. "Imaging"/"Cardiac" are
# test categories, not samples -- "sample: imaging" would be nonsense.
_SPECIMENS = {"blood", "urine", "stool", "serum", "saliva", "swab", "plasma"}


def insufficient_information_reply(lang: str) -> str:
    if lang == "hi":
        return "माफ़ कीजिए, ठीक से सुन नहीं पाई। क्या आप फिर से थोड़ा साफ़ बोलेंगे?"
    return "Sorry, I didn't catch that clearly enough. Could you say it again?"


def missing_slot_prompt(intent: str, missing: str, lang: str) -> str:
    if lang == "hi":
        return _HI_MISSING.get((intent, missing), _HI_MISSING_DEFAULT)
    return _EN_MISSING.get((intent, missing), _EN_MISSING_DEFAULT)


def _script_ok(text: str, lang: str) -> bool:
    if lang == "hi":
        return bool(_DEVANAGARI.search(text))
    return bool(_LATIN.search(text)) and not _DEVANAGARI.search(text)


def _suggestions(result: dict, lang: str) -> list[str]:
    """did_you_mean mixes English names and aliases in every script. Keep
    only the ones this language's voice can say, or the caller hears a
    list with the names missing."""
    scripted = result.get(f"did_you_mean_{lang}") or []
    if scripted:
        return list(scripted)
    return [s for s in (result.get("did_you_mean") or []) if _script_ok(s, lang)]


def _test_name(slots: dict, result: dict, lang: str) -> str:
    if lang == "hi":
        heard = slots.get("test_name") or ""
        return result.get("test_name_hi") or (heard if _DEVANAGARI.search(heard) else "") or "यह"
    return result.get("test_name") or slots.get("test_name") or "the test"


def doctor_name(slots: dict, result: dict, lang: str) -> str:
    """The doctor as THIS language should say them. Public because the booking-lookup reply in
    reply_templates needs the same rule (story "Caller asks what they have booked")."""
    return _doctor_name(slots, result, lang)


def _doctor_name(slots: dict, result: dict, lang: str) -> str:
    if lang == "hi":
        alias = result.get("doctor_name_hi")
        if alias:
            return f"डॉक्टर {alias}"
        heard = slots.get("doctor_name") or ""
        return heard if _DEVANAGARI.search(heard) else "डॉक्टर"
    full = result.get("doctor_name") or slots.get("doctor_name") or ""
    surname = full.replace("Dr.", "").replace("Dr", "").split()[-1:] if full else []
    return f"Doctor {surname[0]}" if surname else "the doctor"


def _query(slots: dict, key: str, lang: str) -> str:
    q = slots.get(key) or ""
    return q if _script_ok(q, lang) else ""


def test_rate_reply(slots: dict, result: dict, lang: str) -> str:
    hi = lang == "hi"
    if not result.get("found"):
        return _not_found_test(slots, result, lang)
    name = _test_name(slots, result, lang)
    rate = result["rate_inr"]
    hours = result.get("report_time_hours")
    sample = (result.get("sample_type") or "").strip()
    if hi:
        reply = f"{name} टेस्ट की कीमत {rate} रुपये है।"
        sentence = sample_sentence(sample, "hi")
        if sentence:
            reply += f" {sentence}"
        if hours:
            reply += f" रिपोर्ट {hours} घंटे में मिल जाएगी।"
        return reply
    reply = f"The price of the {name} test is {rate} rupees."
    sentence = sample_sentence(sample, "en")
    if sentence:
        reply += f" {sentence}"
    if hours:
        reply += f" You will get the report within {hours} hours."
    return reply


# story: "Caller asks the price of a test" -- several tests in one question, and their total.
_BENGALI = re.compile(r"[ঀ-৿]")


def _rupees(value) -> str:
    """400, never 400.0: the figure is spoken as written."""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def spoken_test_name(query: str, result: dict, lang: str) -> str:
    """A FOUND test as this language's voice can say it: the clinic's own alias in that script, else
    the caller's words when they are in that script, else the catalogue name."""
    query = query or ""
    if lang == "bn":
        own = query if _BENGALI.search(query) and not _LATIN.search(query) else ""
        return result.get("test_name_bn") or own or result.get("test_name") or query
    if lang == "hi":
        own = query if _DEVANAGARI.search(query) else ""
        return result.get("test_name_hi") or own or result.get("test_name") or query
    return result.get("test_name") or query


def unfound_test(query: str, result: dict, lang: str) -> dict:
    """What the multi-test reply needs to say about a test the clinic did not pin down: the caller's
    words only if this language can say them, and the clinic's own suggestions in this script."""
    script_ok = (
        bool(_BENGALI.search(query or "")) and not _LATIN.search(query or "")
        if lang == "bn"
        else _script_ok(query or "", lang)
    )
    return {
        "query": query if script_ok else "",
        "suggestions": _suggestions(result, lang),
        "ambiguous": bool(result.get("ambiguous")),
    }


def test_confirm_prompt(spoken_names: list[str], action: str, lang: str) -> str:
    """Read the tests back and ask for a yes BEFORE any price is spoken (the acceptance layer).

    Live 2026-10-08: ASR lost the names, the model supplied two of its own, and the caller was quoted a real
    total for tests they never asked for. Nothing is priced now until the caller has heard which tests the agent
    thinks they said. `spoken_names` are the caller's own words for them (agent/entity_grounding.forms)."""
    listed = spoken_list(spoken_names, lang)
    if lang == "bn":
        # A name that already ends in "টেস্ট" ("ইকো টেস্ট", "ইউরিন টেস্ট") must not get another: "ইকো টেস্ট টেস্টের দাম".
        named_test = listed.endswith("টেস্ট")
        if action == "book_test":
            return f"আপনি {listed} করবেন, ঠিক তো?" if named_test else f"আপনি {listed} টেস্ট করবেন, ঠিক তো?"
        return (
            f"আপনি {listed}ের দাম জানতে চান, ঠিক তো?"
            if named_test
            else f"আপনি {listed} টেস্টের দাম জানতে চান, ঠিক তো?"
        )
    if lang == "hi":
        named_test = listed.endswith("टेस्ट")
        if action == "book_test":
            return f"आप {listed} कराएँगे, ठीक है ना?" if named_test else f"आप {listed} टेस्ट कराएँगे, ठीक है ना?"
        return (
            f"आप {listed} का दाम जानना चाहते हैं, ठीक है ना?"
            if named_test
            else f"आप {listed} टेस्ट का दाम जानना चाहते हैं, ठीक है ना?"
        )
    return (
        f"You want the {listed} tests, is that right?"
        if action == "book_test"
        else f"You'd like the price of {listed}, is that right?"
    )


def which_tests_prompt(count: int | None, lang: str) -> str:
    """Which tests -- asked, never guessed. `count` is how many the caller said they wanted (a different fact
    from WHICH, see agent/entity_grounding.spoken_count): knowing it makes the question specific."""
    if lang == "bn":
        if count:
            return f"আপনি {_COUNT_BN.get(count, count)} টেস্টের কথা বলছেন -- কোন কোন টেস্ট, একটু নাম বলবেন?"
        return "কোন কোন টেস্টের কথা বলছেন, একটু নাম বলবেন?"
    if lang == "hi":
        if count:
            return f"आप {_COUNT_HI.get(count, count)} टेस्ट की बात कर रहे हैं -- कौन कौन से, नाम बताएँगे?"
        return "कौन कौन से टेस्ट की बात कर रहे हैं, नाम बताएँगे?"
    if count:
        return f"You mentioned {_COUNT_EN.get(count, count)} tests -- which ones? Could you say the names?"
    return "Which tests do you mean? Could you say the names?"


def test_choice_prompt(sure: list[str], candidates: list[str], both: bool, lang: str, reasked: bool = False) -> str:
    """Two or three tests fit the words just heard about equally well ("টিএসএ": TSH or PSA): ask which, never pick. The tests
    already understood are said first, so the caller hears they are kept and answers only the doubt. `both` is the case of
    two stretches of words that each fit the same two tests: one question, "both?". Said in the caller's language, with the
    clinic's own spoken names; one question per turn."""
    have = spoken_list(sure, lang)

    def options(word: str) -> str:
        if len(candidates) < 2:
            return candidates[0] if candidates else ""
        return ", ".join(candidates[:-1]) + f" {word} " + candidates[-1]

    if lang == "bn":
        if reasked:
            return f"ঠিক বুঝতে পারিনি -- {options('নাকি')}? নামটা একটু বলবেন?"
        lead = f"{have} বুঝেছি। " if have else ""
        if both:
            return f"{lead}{spoken_list(candidates, lang).strip()} -- দুটোই বলছেন?"
        return f"{lead}আরেকটা টেস্ট -- {options('নাকি')}?" if have else f"কোনটা -- {options('নাকি')}?"
    if lang == "hi":
        if reasked:
            return f"ठीक से समझ नहीं पाई -- {options('या')}? नाम बताएँगे?"
        lead = f"{have} समझ गई। " if have else ""
        if both:
            return f"{lead}{spoken_list(candidates, lang).strip()} -- दोनों?"
        return f"{lead}एक और टेस्ट -- {options('या')}?" if have else f"कौन सा -- {options('या')}?"
    if reasked:
        return f"Sorry -- {options('or')}? Please say the name."
    lead = f"I have {have}. " if have else ""
    if both:
        return f"{lead}{spoken_list(candidates, lang).strip()} -- do you mean both?"
    return f"{lead}And one more -- {options('or')}?" if have else f"Which one -- {options('or')}?"


_COUNT_BN = {1: "একটা", 2: "দুটো", 3: "তিনটে", 4: "চারটে", 5: "পাঁচটা"}
_COUNT_HI = {1: "एक", 2: "दो", 3: "तीन", 4: "चार", 5: "पांच"}
_COUNT_EN = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


TEST_LIST_PAGE = 5  # tests read per turn: a phone line cannot hold 34 names and prices in one breath


def test_group_reply(label: dict, tests: list[dict], lang: str, mode: str = "price") -> str:
    """story: "Caller asks for a group of tests" -- "হার্টের টেস্ট", "সুগারের পরীক্ষা", "ভিটামিন টেস্ট".

    `tests` are the group's members as GET /api/v1/tests gave them (name and rate): every name and price said is the
    database's, substituted here, never the model's (CLAUDE.md section 1). `label` is how this group is said in each
    language (agent/test_groups.TestGroup.label). mode "price": names with prices; "prep": names only, and which one's
    preparation; "book": names with prices, and which one to book."""
    lang = lang if lang in ("bn", "hi", "en") else "bn"
    n = len(tests)
    if mode == "prep":
        items = [spoken_test_name("", t, lang) for t in tests]
    elif lang == "bn":
        items = [f"{spoken_test_name('', t, 'bn')} {_rupees(t['rate_inr'])} টাকা" for t in tests]
    elif lang == "hi":
        items = [f"{spoken_test_name('', t, 'hi')} {_rupees(t['rate_inr'])} रुपये" for t in tests]
    else:
        items = [f"{spoken_test_name('', t, 'en')}, {_rupees(t['rate_inr'])} rupees" for t in tests]
    body = spoken_list(items, lang) + ("." if lang == "en" else "।")
    word = label.get(lang) or label.get("bn") or ""
    head = {
        "bn": f"{word} টেস্ট আমাদের এখানে {n}টা আছে:" if n > 1 else f"{word} টেস্ট আমাদের এখানে একটাই:",
        "hi": f"{word} टेस्ट हमारे यहाँ {n} हैं:" if n > 1 else f"{word} टेस्ट हमारे यहाँ एक ही है:",
        "en": f"We have {n} {word} tests:" if n > 1 else f"We have one {word} test:",
    }[lang]
    tail = {
        ("bn", "price"): "কোনো টেস্টের নাম বললে বিস্তারিত বলতে পারি।",
        ("bn", "prep"): "কোনটার প্রস্তুতি জানতে চান?",
        ("bn", "book"): "কোনটা করাতে চান?",
        ("hi", "price"): "किसी टेस्ट का नाम बताएँ तो पूरी जानकारी दे सकती हूँ।",
        ("hi", "prep"): "किस टेस्ट की तैयारी जाननी है?",
        ("hi", "book"): "कौन सा करवाना चाहेंगे?",
        ("en", "price"): "Name one and I can tell you more.",
        ("en", "prep"): "Which one's preparation would you like to know?",
        ("en", "book"): "Which one would you like to book?",
    }[(lang, mode if mode in ("price", "prep", "book") else "price")]
    return f"{head} {body} {tail}"


def test_list_reoffer(lang: str) -> str:
    """The test list is open and the caller said something that could not be placed (a garbled "পরের টেস্টগুলো"): say
    so once, and offer the same two ways on as before -- never a bare "I did not understand" that drops the list."""
    return {
        "bn": "ঠিক বুঝতে পারিনি। আরও টেস্ট শুনবেন, নাকি কোনো টেস্টের নাম বলবেন?",
        "hi": "ठीक से समझ नहीं पाई। और टेस्ट सुनेंगे, या किसी टेस्ट का नाम बताएँगे?",
        "en": "Sorry, I did not catch that. Shall I go on with the tests, or would you like to name one?",
    }.get(lang) or "ঠিক বুঝতে পারিনি। আরও টেস্ট শুনবেন, নাকি কোনো টেস্টের নাম বলবেন?"


def test_list_page(listing: dict, offset: int, lang: str) -> tuple[str, int | None]:
    """story: "Caller asks which lab tests are available" -- the next few tests, each with its price, and where to go
    from there. -> (reply, the offset to continue from, or None when this was the last of them).

    `listing` is clinic-api's GET /api/v1/tests answer: every name and price is the database's, substituted here,
    never the model's (CLAUDE.md section 1). The first page says how many tests there are, so the caller knows what
    "more" means."""
    tests = listing.get("tests") or []
    page = tests[offset : offset + TEST_LIST_PAGE]
    if not page:
        return (
            {
                "bn": "এই মুহূর্তে টেস্টের তালিকাটা পাচ্ছি না। কোনো টেস্টের নাম বললে দামটা বলে দিতে পারি।",
                "hi": "अभी टेस्ट की सूची नहीं मिल रही। किसी टेस्ट का नाम बताएँ तो उसका दाम बता सकती हूँ।",
                "en": "I can't get the list of tests just now. If you name a test, I can tell you its price.",
            }[lang],
            None,
        )
    items = {
        "bn": [f"{spoken_test_name('', t, 'bn')} {_rupees(t['rate_inr'])} টাকা" for t in page],
        "hi": [f"{spoken_test_name('', t, 'hi')} {_rupees(t['rate_inr'])} रुपये" for t in page],
        "en": [f"{spoken_test_name('', t, 'en')}, {_rupees(t['rate_inr'])} rupees" for t in page],
    }[lang]
    body = spoken_list(items, lang) + ("." if lang == "en" else "।")
    following = offset + len(page)
    more = following < len(tests)
    parts = []
    if offset == 0:
        n = len(tests)
        parts.append({"bn": f"আমাদের এখানে মোট {n}টা টেস্ট হয়।", "hi": f"हमारे यहाँ कुल {n} टेस्ट होते हैं।",
                      "en": f"We do {n} tests in all."}[lang])
    parts.append(body)
    if more:
        parts.append({"bn": "আরও শুনবেন, নাকি কোনো টেস্টের নাম বলবেন?", "hi": "और सुनेंगे, या किसी टेस्ट का नाम बताएँगे?",
                      "en": "Shall I go on, or would you like to name a test?"}[lang])
    else:
        parts.append({"bn": "এই ছিল সব টেস্ট। কোনো টেস্টের দাম বা প্রস্তুতি জানতে চাইলে নামটা বলুন।",
                      "hi": "ये थे सारे टेस्ट। किसी टेस्ट का दाम या तैयारी जाननी हो तो नाम बताइए।",
                      "en": "That's all of them. Name a test if you'd like its price or preparation."}[lang])
    return " ".join(parts), following if more else None


def multi_test_rate_reply(found: list[tuple[str, object]], missing: list[dict], lang: str) -> str:
    """"CBC is 400 rupees, Lipid Profile 650 rupees and TSH 350 rupees. Altogether that is 1400 rupees."

    Each price is the clinic's catalogue figure and the total is their sum, worked out here in code --
    the model never sees a number (CLAUDE.md section 1). A total is only said over two or more FOUND
    tests, and when a test was not found it is labelled as the total of the ones found, never as the
    total of everything the caller asked for. A test the clinic could not pin down gets its own
    sentence: the not-found path, with the clinic's suggestions when it has any."""
    parts: list[str] = []
    if found:
        per_test = {
            "bn": [f"{name} {_rupees(rate)} টাকা" for name, rate in found],
            "hi": [f"{name} {_rupees(rate)} रुपये" for name, rate in found],
            "en": [f"{name} is {_rupees(rate)} rupees" for name, rate in found],
        }.get(lang)
        parts.append(spoken_list(per_test, lang) + ("." if lang == "en" else "।"))
        if len(found) > 1:
            total = _rupees(sum(float(rate) for _, rate in found))
            if missing:
                parts.append(
                    {
                        "bn": f"যে টেস্টগুলো পেলাম, সেগুলো মিলিয়ে মোট {total} টাকা।",
                        "hi": f"जो टेस्ट मिले, उनका कुल {total} रुपये है।",
                        "en": f"The ones I found come to {total} rupees altogether.",
                    }[lang]
                )
            else:
                parts.append(
                    {
                        "bn": f"সব মিলিয়ে মোট {total} টাকা।",
                        "hi": f"सब मिलाकर कुल {total} रुपये।",
                        "en": f"Altogether that is {total} rupees.",
                    }[lang]
                )
    for miss in missing:
        q, sugg = miss["query"], miss["suggestions"]
        named = "'" + q + "'" if q else ""
        if lang == "bn":
            if miss["ambiguous"] and sugg:
                lead = named + " বলতে " if q else ""
                parts.append(lead + "একাধিক টেস্ট আছে -- " + " নাকি ".join(sugg) + ", কোনটা?")
            elif sugg:
                lead = named + " নামে টেস্ট" if q else "একটা টেস্ট"
                parts.append(lead + " খুঁজে পাইনি। আপনি কি " + ", ".join(sugg) + " বলতে চাইছেন?")
            else:
                lead = named + " নামে কোনো টেস্ট" if q else "একটা টেস্ট"
                parts.append(lead + " আমাদের তালিকায় নেই।")
        elif lang == "hi":
            if miss["ambiguous"] and sugg:
                lead = named + " के " if q else ""
                parts.append(lead + "एक से ज़्यादा टेस्ट हैं -- " + " या ".join(sugg) + ", कौन सा?")
            elif sugg:
                lead = named + " नाम का टेस्ट" if q else "एक टेस्ट"
                parts.append(lead + " नहीं मिला। क्या आप " + ", ".join(sugg) + " कहना चाह रहे हैं?")
            else:
                lead = named + " नाम का कोई टेस्ट" if q else "एक टेस्ट"
                parts.append(lead + " हमारी सूची में नहीं है।")
        else:
            if miss["ambiguous"] and sugg:
                lead = "For " + named + ", " if q else ""
                parts.append(lead + "I found more than one test -- " + " or ".join(sugg) + ", which one?")
            elif sugg:
                what = "a test called " + named if q else "one of the tests"
                parts.append("I couldn't find " + what + ". Did you mean " + ", ".join(sugg) + "?")
            else:
                parts.append(("There is no test called " + named if q else "One of the tests is not") + " on our list.")
    return " ".join(parts)


def _not_found_test(slots: dict, result: dict, lang: str) -> str:
    q = _query(slots, "test_name", lang)
    sugg = _suggestions(result, lang)
    if result.get("ambiguous") and sugg:
        # KCD-446: exists, but matched more than one row equally well --
        # distinct framing from "not found" below, which would be false.
        return (
            f"एक से ज़्यादा टेस्ट मिले -- कौन सा: {' या '.join(sugg)}?"
            if lang == "hi"
            else f"I found more than one test -- which one did you mean: {' or '.join(sugg)}?"
        )
    # KCD-lay-terms: see reply_templates.test_rate_reply's Bengali counterpart for why this invitation is here.
    if lang == "hi":
        if sugg:
            return (
                f"'{q}' नाम का टेस्ट नहीं मिला। क्या आप {', '.join(sugg)} कहना चाह रहे हैं?"
                if q
                else f"वह टेस्ट नहीं मिला। क्या आप {', '.join(sugg)} कहना चाह रहे हैं?"
            )
        base = f"माफ़ कीजिए, '{q}' नाम का कोई टेस्ट हमारी सूची में नहीं है।" if q else "माफ़ कीजिए, वह टेस्ट हमारी सूची में नहीं है।"
        return f"{base} कोई और टेस्ट या डॉक्टर की अपॉइंटमेंट बुक करनी है?"
    if sugg:
        return (
            f"I couldn't find a test called '{q}'. Did you mean: {', '.join(sugg)}?"
            if q
            else f"I couldn't find that test. Did you mean: {', '.join(sugg)}?"
        )
    base = f"Sorry, there is no test called '{q}' on our list." if q else "Sorry, that test is not on our list."
    return f"{base} Is there any other test or doctor's appointment you'd like to book?"


def _not_found_doctor(slots: dict, result: dict, lang: str) -> str:
    hi = lang == "hi"
    q = _query(slots, "doctor_name", lang)
    sugg = _suggestions(result, lang)
    if result.get("ambiguous") and sugg:
        # Doctor-side counterpart of test_rate_reply's ambiguous framing.
        return (
            f"एक से ज़्यादा डॉक्टर मिले -- किनकी बात कर रहे हैं, {' या '.join(sugg)}?"
            if hi
            else f"I found more than one doctor -- did you mean {' or '.join(sugg)}?"
        )
    # KCD-lay-terms: see reply_templates.doctor_availability_reply's Bengali counterpart for why this invitation
    # is here, and why it needs no new state -- department_query already handles the caller's next turn.
    if hi:
        if sugg:
            return (
                f"'{q}' नाम के डॉक्टर नहीं मिले। क्या आप {', '.join(sugg)} कहना चाह रहे हैं?"
                if q
                else f"वह डॉक्टर नहीं मिले। क्या आप {', '.join(sugg)} कहना चाह रहे हैं?"
            )
        base = (
            f"माफ़ कीजिए, '{q}' नाम के कोई डॉक्टर हमारे यहाँ नहीं हैं।"
            if q
            else "माफ़ कीजिए, इस नाम के कोई डॉक्टर हमारे यहाँ नहीं हैं।"
        )
        return f"{base} आपको क्या तकलीफ़ है बताइए, तो मैं सही विभाग के डॉक्टर बता सकूँगी।"
    if sugg:
        return (
            f"I couldn't find a doctor named '{q}'. Did you mean {', '.join(sugg)}?"
            if q
            else f"I couldn't find that doctor. Did you mean {', '.join(sugg)}?"
        )
    base = f"Sorry, we don't have a doctor named '{q}'." if q else "Sorry, we don't have a doctor by that name."
    return f"{base} Tell me what's troubling you, and I can suggest the right doctor."


# story: "Caller asks when a doctor sits"
def chamber_days_reply(name: str, slots: dict, result: dict, lang: str) -> str:
    """"Doctor Sen sits on Monday, Wednesday and Friday, 10:00-12:00. The next sitting is Thursday,
    2026-10-08." -- for a caller who asked WHEN a doctor sits, without a day of their own. Every word
    of it is the clinic's: the weekdays and the next date from the availability answer, the hours
    only when the clinic says every sitting has the same ones. "" (the caller hears the plain
    next-date answer instead) when a day was asked for, or the clinic sent no weekdays."""
    days = [d for d in (result.get("chamber_days") or []) if isinstance(d, int) and 0 <= d <= 6]
    if slots.get("date") or not days or not result.get("available"):
        return ""
    listed = spoken_list([WEEKDAY_WORDS.get(lang, WEEKDAY_WORDS["bn"])[d] for d in days], lang)
    hours = result.get("regular_chamber_hours")
    next_day = spoken_day(result.get("date"), lang) if result.get("date") else ""
    if lang == "bn":
        reply = f"{name} {listed} বসেন" + (f", সময় {hours}।" if hours else "।")
        return reply + (f" পরের বসা {next_day}।" if next_day else "")
    if lang == "hi":
        reply = f"{name} {listed} को बैठते हैं" + (f", समय {hours}।" if hours else "।")
        return reply + (f" अगली बार वे {next_day} को बैठेंगे।" if next_day else "")
    reply = f"{name} sits on {listed}" + (f", {hours}." if hours else ".")
    return reply + (f" The next sitting is {next_day}." if next_day else "")


def doctor_availability_reply(slots: dict, result: dict, lang: str) -> str:
    hi = lang == "hi"
    if not result.get("found"):
        return _not_found_doctor(slots, result, lang)

    name = _doctor_name(slots, result, lang)
    days = chamber_days_reply(name, slots, result, lang)
    if days:
        return days
    if result.get("available"):
        hours = result.get("chamber_hours", "")
        date = result.get("date")
        if hi:
            when = f" {date} को" if date else " आज"
            return f"जी हाँ,{when} {name} {hours} चेंबर में रहेंगे।"
        when = f" on {date}" if date else " today"
        return f"Yes, {name} will be in the chamber{when}, {hours}."

    next_date = result.get("next_available_date")
    if next_date:
        if hi:
            return f"{name} उस दिन नहीं बैठेंगे। उनका अगला दिन {spoken_day(next_date, 'hi')} है।"
        return f"{name} does not sit on that day. Their next sitting is {spoken_day(next_date, 'en')}."
    if hi:
        return f"{name} अभी किसी तय दिन नहीं बैठ रहे हैं। आप हमारे काउंटर पर पूछ सकते हैं।"
    return f"{name} is not sitting on any fixed day right now. You can ask at our counter."


def test_prep_reply(slots: dict, result: dict, lang: str) -> str:
    if not result.get("found"):
        return _not_found_test(slots, result, lang)
    name = _test_name(slots, result, lang)
    # The lab-test table decides: its preparation text if it has one; else its fasting_required column.
    if result.get("fasting_required"):
        default = (
            "इस टेस्ट के लिए खाली पेट रहना होगा। कितने घंटे, यह काउंटर पर पूछ लें।"
            if lang == "hi"
            else "Fasting is required for this test. Please ask the counter how many hours."
        )
    else:
        default = (
            "इस टेस्ट के लिए किसी ख़ास तैयारी की ज़रूरत नहीं है।"
            if lang == "hi"
            else "No special preparation is needed for this test."
        )
    instructions = result.get("prep_instructions") or default
    return f"{name} टेस्ट के लिए: {instructions}" if lang == "hi" else f"For the {name} test: {instructions}"


def clinic_faq_reply(slots: dict, result: dict, lang: str) -> str:
    fallback = (
        "माफ़ कीजिए, इस बारे में अभी सही जानकारी नहीं दे सकती। कृपया काउंटर पर संपर्क करें।"
        if lang == "hi"
        else "Sorry, I can't give you accurate information on that right now. Please contact the counter."
    )
    if not result.get("found"):
        return fallback
    return result.get("answer") or fallback


# story: "Caller says tomorrow, day after, or next Monday"
def date_check_prompt(date_iso: str, said: str | None, lang: str) -> str:
    """See reply_templates.date_check_prompt."""
    hi = lang == "hi"
    spoken = spoken_day(date_iso, lang)
    if hi:
        head = f"{said} मतलब {spoken}" if said else spoken
        return f"{head}। सही है?"
    head = f"{said} means {spoken}" if said else spoken
    return f"{head}. Is that right?"


# story: "Caller says tomorrow, day after, or next Monday"
def date_ask_prompt(candidates, lang: str) -> str:
    """See reply_templates.date_ask_prompt."""
    hi = lang == "hi"
    days = [spoken_day(d, lang) for d in list(candidates)[:2]]
    if len(days) < 2:
        if not days:
            return "किस तारीख की बात कर रहे हैं?" if hi else "Which date do you mean?"
        return (
            f"क्या आप {days[0]} की बात कर रहे हैं, या कोई और दिन?"
            if hi
            else f"Do you mean {days[0]}, or another day?"
        )
    return (
        f"क्या आप {days[0]} की बात कर रहे हैं, या {days[1]} की?"
        if hi
        else f"Do you mean {days[0]}, or {days[1]}?"
    )


# story: "Caller names only a doctor"
# story: "Caller moves an existing appointment"
def move_day_prompt(result: dict, lang: str) -> str:
    """Which day to move to, with the doctor's own sitting days so the caller is not guessing:
    "Doctor Sen sits on Tuesday, Thursday and Saturday, 10:00-12:00. The next sitting is Thursday,
    2026-10-08. Which day shall I move it to?" -- every day and hour from the clinic's availability
    answer (chamber_days_reply). Without that answer, only the question."""
    if lang == "bn":
        alias = result.get("doctor_name_bn")
        name = f"ডাঃ {alias}" if alias else (result.get("doctor_name") or "ডাক্তার")
        ask = "কোন দিনে সরাতে চান?"
    else:
        name = _doctor_name({}, result, lang)
        ask = "किस दिन पर ले जाना चाहते हैं?" if lang == "hi" else "Which day would you like to move it to?"
    days = chamber_days_reply(name, {}, result, lang) if result.get("found") else ""
    return f"{days} {ask}".strip()


def move_same_day_prompt(booking: dict, lang: str) -> str:
    """The "new" day the caller gave is the day the booking is already on. Usually they are pointing at the
    booking ("the Saturday one") and want it gone, not moved -- Bengali "সরাতে চাই" is both "move" and "remove".
    Asked, never guessed: move to another day, or cancel? Live 2026-10-07: the caller gave the booking's own day
    three times and heard "which day shall I move it to?" three times. `booking` is clinic-api's lookup row."""
    day = spoken_day(booking.get("date"), lang)
    time = booking.get("time_slot") or ""
    if lang == "bn":
        when = f"{day}, {time}" if time else day
        return f"আপনার অ্যাপয়েন্টমেন্ট তো ওই দিনেই আছে -- {when}। অন্য কোনো দিনে সরাতে চান, নাকি অ্যাপয়েন্টমেন্টটা বাতিল করতে চান?"
    if lang == "hi":
        when = f"{day}, {time}" if time else day
        return f"आपकी अपॉइंटमेंट तो उसी दिन है -- {when}। किसी और दिन पर ले जाना चाहते हैं, या अपॉइंटमेंट कैंसल करनी है?"
    when = f"{day} at {time}" if time else day
    return f"Your appointment is already on that day -- {when}. Would you like to move it to another day, or cancel it?"


def move_time_prompt(hours: str, lang: str) -> str:
    """The time given is outside the doctor's chamber on the new day: say the chamber hours and ask again."""
    if lang == "bn":
        return f"ওই দিন চেম্বারের সময় {hours}। এর মধ্যে কোন সময়ে করব?"
    if lang == "hi":
        return f"उस दिन चैंबर का समय {hours} है। इसमें किस समय करूँ?"
    return f"That day the chamber is {hours}. What time within that shall I make it?"


def doctor_day_unavailable_prompt(result: dict, lang: str, sitting_over: bool = False) -> str:
    """See reply_templates.doctor_day_unavailable_prompt."""
    hi = lang == "hi"
    doctor = _doctor_name({}, result, lang)
    if sitting_over:
        refusal = f"{doctor} का आज का चैंबर का समय निकल चुका है" if hi else f"{doctor}'s chamber today is already over"
    else:
        refusal = f"{doctor} उस दिन नहीं बैठते" if hi else f"{doctor} doesn't sit that day"
    next_date = result.get("next_available_date") or result.get("date")
    if not next_date:
        return (
            f"{refusal}, और अगले दो हफ़्तों में भी नहीं। किसी और डॉक्टर के बारे में बताऊँ?"
            if hi
            else f"{refusal}, or in the next two weeks. Shall I tell you about another doctor?"
            if not sitting_over
            else f"{refusal}, and they don't sit in the next two weeks. Shall I tell you about another doctor?"
        )
    hours = result.get("chamber_hours")
    day = spoken_day(next_date, lang)
    if hi:
        hours_clause = f", चैंबर का समय {hours}" if hours else ""
        return f"{refusal}। उनका अगला दिन {day} है{hours_clause}। उसी दिन बुक करूँ, या कोई और दिन?"
    hours_clause = f", in the chamber {hours}" if hours else ""
    return f"{refusal}. Their next sitting is {day}{hours_clause}. Shall I book that day, or another day?"


def doctor_offer_prompt(result: dict, lang: str, ask_time: bool = False) -> str:
    """See reply_templates.doctor_offer_prompt."""
    hi = lang == "hi"
    doctor = _doctor_name({}, result, lang)
    next_date = result.get("next_available_date") or result.get("date")
    if not next_date:
        return (
            f"{doctor} अगले दो हफ़्तों में नहीं बैठ रहे। किसी और डॉक्टर के बारे में बताऊँ?"
            if hi
            else f"{doctor} has no sitting in the next two weeks. Shall I tell you about another doctor?"
        )
    hours = result.get("chamber_hours")
    day = spoken_day(next_date, lang)
    if hi:
        hours_clause = f", चैंबर का समय {hours}" if hours else ""
        time_clause = " और किस समय?" if ask_time else ""
        return (
            f"जी हाँ, {doctor}। उनका अगला दिन {day} है{hours_clause}। "
            f"उसी दिन बुक करूँ, या कोई और दिन?{time_clause}"
        )
    hours_clause = f", in the chamber {hours}" if hours else ""
    time_clause = " And what time?" if ask_time else ""
    return (
        f"Yes, {doctor}. Their next sitting is {day}{hours_clause}. "
        f"Shall I book that day, or another day?{time_clause}"
    )


# story: "Caller asks for the earliest available appointment"
def earliest_slots_prompt(result: dict, lang: str) -> str:
    """See reply_templates.earliest_slots_prompt."""
    hi = lang == "hi"
    doctor = _doctor_name({}, result, lang)
    day = spoken_day(result.get("date"), lang)
    time_slot = result.get("time_slot")
    alternatives = result.get("alternatives") or []
    # Short sentences on purpose -- see reply_templates.earliest_slots_prompt.
    if hi:
        more = f" उसी दिन {' और '.join(alternatives)} भी खाली है।" if alternatives else ""
        choose = "कौन सा बुक करूँ, पहला या दूसरा?" if len(alternatives) == 1 else (
            "कौन सा बुक करूँ, पहला, दूसरा या तीसरा?" if alternatives else "क्या वही बुक कर दूँ?"
        )
        return (
            f"{doctor} सबसे पहले {day} को बैठेंगे। {time_slot} खाली है।{more} "
            f"{choose} कोई और दिन चाहिए तो बताइए।"
        )
    more = f" That day {' and '.join(alternatives)} are also free." if alternatives else ""
    choose = "Which one shall I book, the first or the second?" if len(alternatives) == 1 else (
        "Which one shall I book, the first, the second or the third?" if alternatives else "Shall I book that one?"
    )
    return (
        f"{doctor} is next in on {day}. {time_slot} is free.{more} "
        f"{choose} You can also name another day."
    )


# story: "Caller asks for the earliest available appointment"
def earliest_none_prompt(result: dict, lang: str, offer_callback: bool = True) -> str:
    """See reply_templates.earliest_none_prompt."""
    hi = lang == "hi"
    doctor = _doctor_name({}, result, lang)
    if not offer_callback:
        return (
            f"{doctor} के पास अगले दो हफ़्तों में कोई खाली समय नहीं है। आप काउंटर से संपर्क कर सकते हैं।"
            if hi
            else f"{doctor} has no free time in the next two weeks. You can contact the counter."
        )
    return (
        f"{doctor} के पास अगले दो हफ़्तों में कोई खाली समय नहीं है। समय खाली होने पर क्लिनिक से कोई "
        f"आपको फ़ोन कर सकता है। उसके लिए एक फ़ोन नंबर बताइए?"
        if hi
        else f"{doctor} has no free time in the next two weeks. When a time opens, someone from "
        f"the clinic can call you. Could you give me a phone number for that?"
    )


# story: "Requested slot is already taken"
def slot_taken_reply(slots: dict, result: dict, lang: str) -> str:
    """See reply_templates.slot_taken_reply. The other-day alternative NAMES its day: it is not
    the day the caller asked about, and offering it as a bare time let them accept a slot on a day
    they never agreed to."""
    hi = lang == "hi"
    alts = result.get("alternative_slots") or []
    other = result.get("other_day_slot") or {}
    parts = []
    if alts:
        joined = " या ".join(alts) if hi else " or ".join(alts)
        parts.append(f"उस दिन {joined} खाली है" if hi else f"that day {joined} is free")
    if other.get("date"):
        day = spoken_day(other["date"], lang)
        parts.append(
            f"{day} को उसी समय {other['time_slot']} खाली है"
            if hi
            else f"{day} at the same time, {other['time_slot']}, is free"
        )
    if not parts:
        return (
            "वह समय बुक हो चुका है, और आसपास कोई समय खाली नहीं है।"
            if hi
            else "That time is already booked, and nothing nearby is free."
        )
    joined = ", या ".join(parts) if hi else ", or ".join(parts)
    return (
        f"वह समय बुक हो चुका है, लेकिन {joined}। कौन सा बुक करूँ?"
        if hi
        else f"That time is already booked, but {joined}. Which one shall I book?"
    )


def booking_reply(slots: dict, result: dict, lang: str) -> str:
    hi = lang == "hi"
    if result.get("success"):
        doctor = _doctor_name(slots, result, lang)
        if hi:
            return (
                f"आपकी अपॉइंटमेंट कन्फ़र्म हो गई है। {doctor}, {result['date']}, "
                f"समय {result['time_slot']}। कन्फ़र्मेशन नंबर: {result['confirmation_id']}।"
            )
        return (
            f"Your appointment is confirmed. {doctor}, {result['date']}, "
            f"at {result['time_slot']}. Your confirmation number is {result['confirmation_id']}."
        )

    reason = result.get("reason")
    if reason == "slot_taken":
        return slot_taken_reply(slots, result, lang)
    if reason == "doctor_ambiguous":
        names = result.get("did_you_mean_hi" if hi else "did_you_mean") or result.get("did_you_mean") or []
        if hi:
            return (
                f"एक से ज़्यादा डॉक्टर मिले -- आप किसके बारे में पूछ रहे हैं, {' या '.join(names)}?"
                if names
                else "एक से ज़्यादा डॉक्टर मिले। आप किस डॉक्टर के बारे में पूछ रहे हैं, पूरा नाम बताएँगे?"
            )
        return (
            f"I found more than one doctor -- which do you mean, {' or '.join(names)}?"
            if names
            else "I found more than one doctor. Which one do you mean? Please say the full name."
        )
    if reason == "doctor_not_found":
        q = _query(slots, "doctor_name", lang)
        if hi:
            return f"माफ़ कीजिए, '{q}' नाम के कोई डॉक्टर नहीं मिले।" if q else "माफ़ कीजिए, इस नाम के कोई डॉक्टर नहीं मिले।"
        return f"Sorry, I couldn't find a doctor named '{q}'." if q else "Sorry, I couldn't find a doctor by that name."
    if reason == "doctor_not_available_that_day":
        return (
            "माफ़ कीजिए, उस दिन डॉक्टर नहीं बैठते। कोई और दिन बताएँगे?"
            if hi
            else "Sorry, the doctor doesn't sit that day. Could you say another day?"
        )
    slot_refusal = _slot_refusal(result, lang, slots.get("time_slot"))
    if slot_refusal:
        return slot_refusal
    if reason == "date_in_past":
        return (
            "वह तारीख तो निकल चुकी है। अगले हफ़्ते उसी दिन के लिए बुक कर दूँ?"
            if hi
            else "That date has already passed. Shall I book the same weekday next week instead?"
        )
    if reason == "hold_expired":
        return (
            "माफ़ कीजिए, समय रोका नहीं जा सका, थोड़ी देर हो गई। फिर कोशिश करें?"
            if hi
            else "Sorry, that slot couldn't be held in time. Shall we try again?"
        )
    return (
        "माफ़ कीजिए, अपॉइंटमेंट बुक नहीं हो सका। थोड़ी देर बाद फिर कोशिश करें, या काउंटर पर संपर्क करें।"
        if hi
        else "Sorry, I couldn't book the appointment. Please try again shortly, or contact the counter."
    )


# story: "Caller cannot give a contact number"
def no_booking_on_number(phone: str, lang: str) -> str:
    """See reply_templates.no_booking_on_number."""
    spelled = " ".join(phone)
    return (
        f"{spelled} नंबर पर कोई बुकिंग नहीं मिली। कन्फ़र्मेशन नंबर बताएँगे, या जिस नंबर से बुक किया था वह एक बार फिर बताएँगे?"
        if lang == "hi"
        else f"I couldn't find a booking on {spelled}. Could you give me the confirmation number, or say the number you "
        "booked from once more?"
    )


def phone_continue_prompt(so_far: str, lang: str) -> str:
    """See reply_templates.phone_continue_prompt."""
    spelled = " ".join(so_far)
    return (
        f"अभी तक {spelled} मिला है। बाकी अंक बताइए?"
        if lang == "hi"
        else f"So far I have {spelled}. Could you give me the rest of the number?"
    )


# story: "Caller gives everything in one sentence"
# story: "Patient name is misheard"

def _with_title(name: str | None) -> str:
    """ "Dr." exactly once. The canonical name already carries it ("Dr. A. Sen"), so the read-back's own
    "Dr. {name}" said "Dr. Dr. A. Sen" to every English-speaking caller."""
    name = (name or "").strip()
    return name if re.match(r"(?i)^(dr\.?|doctor)\s", name) else f"Dr. {name}"

def booking_confirmation_readback(slots: dict, action: str, lang: str) -> str:
    hi = lang == "hi"
    # The number the confirmation goes to: a contact number given for it wins over the calling line, and with none at
    # all (the caller declined -- story "Caller cannot give a contact number") the clause is left out. It used to be
    # read as the literal "फ़ोन नंबर None" / "phone number None" (live 2026-10-07); Bengali already did this right.
    phone = slots.get("contact_phone") or slots.get("phone")
    hi_phone = f", फ़ोन नंबर {phone}" if phone else ""
    en_phone = f", phone number {phone}" if phone else ""
    if action == "book_appointment":
        if hi:
            return (
                f"तो {slots.get('doctor_name_hi') or slots.get('doctor_name')} डॉक्टर के पास {spoken_day(slots.get('date'), 'hi')} को, "
                f"समय {slots.get('time_slot')} पर, मरीज़ का नाम {slots.get('patient_name')}"
                f"{hi_phone} -- यह अपॉइंटमेंट कन्फ़र्म कर दूँ?"
            )
        return (
            f"So, with {_with_title(slots.get('doctor_name'))} on {spoken_day(slots.get('date'), 'en')} at {slots.get('time_slot')}, "
            f"for {slots.get('patient_name')}{en_phone} -- shall I confirm this?"
        )
    if action == "book_test":
        tests = ", ".join(slots.get("_test_names_display", [])) or ("टेस्ट" if hi else "the tests")
        if hi:
            return (
                f"तो {slots.get('date')} को {tests} -- मरीज़ का नाम {slots.get('patient_name')}"
                f"{hi_phone} -- यह बुकिंग कन्फ़र्म कर दूँ?"
            )
        return (
            f"So {tests} on {slots.get('date')}, for {slots.get('patient_name')}"
            f"{en_phone} -- shall I confirm this booking?"
        )
    if action == "reschedule_appointment":
        return (
            f"तो अपॉइंटमेंट {spoken_day(slots.get('new_date'), 'hi')} को, समय {slots.get('new_time_slot')} पर ले जाऊँ?"
            if hi
            else f"So I'll move the appointment to {spoken_day(slots.get('new_date'), 'en')} at {slots.get('new_time_slot')} -- confirm?"
        )
    if action == "cancel_appointment":
        if slots.get("phone"):  # see reply_templates.booking_confirmation_readback
            spelled = " ".join(slots["phone"])
            return f"{spelled} नंबर से की गई अपॉइंटमेंट रद्द कर दूँ?" if hi else f"Shall I cancel the appointment booked from {spelled}?"
        return "आपकी अपॉइंटमेंट रद्द कर दूँ?" if hi else "Shall I cancel your appointment?"
    if action == "add_test_booking":
        return (
            f"तो आपकी बुकिंग में {slots.get('test_name')} टेस्ट जोड़ दूँ?"
            if hi
            else f"Shall I add the {slots.get('test_name')} test to your booking?"
        )
    return "क्या यह कन्फ़र्म कर दूँ?" if hi else "Shall I confirm this?"


# story: "Caller moves an existing appointment"
def reschedule_reply(result: dict, lang: str) -> str:
    hi = lang == "hi"
    if result.get("success"):
        if hi:
            return (
                f"आपकी अपॉइंटमेंट {result['date']} को, समय {result['time_slot']} पर बदल दी गई है। "
                f"नया कन्फ़र्मेशन नंबर: {result['confirmation_id']}।"
            )
        return (
            f"Your appointment has been moved to {result['date']} at {result['time_slot']}. "
            f"New confirmation number: {result['confirmation_id']}."
        )
    reason = result.get("reason")
    if reason == "not_found":
        return (
            "माफ़ कीजिए, इस कन्फ़र्मेशन नंबर पर कोई अपॉइंटमेंट नहीं मिली।"
            if hi
            else "Sorry, I couldn't find an appointment with that confirmation number."
        )
    if reason == "slot_taken":
        alts = result.get("alternative_slots") or []
        if alts:
            return (
                f"वह समय बुक हो चुका है। ये समय खाली हैं: {', '.join(alts)}। कौन सा चाहिए?"
                if hi
                else f"That time is already booked. These times are free: {', '.join(alts)}. Which one would you like?"
            )
        return (
            "वह समय बुक हो चुका है। आपकी पुरानी अपॉइंटमेंट वैसी ही है।"
            if hi
            else "That time is already booked. Your original appointment is unchanged."
        )
    slot_refusal = _slot_refusal(result, lang)
    if slot_refusal:
        return slot_refusal
    return (
        "माफ़ कीजिए, अपॉइंटमेंट बदली नहीं जा सकी। आपकी पुरानी अपॉइंटमेंट वैसी ही है।"
        if hi
        else "Sorry, I couldn't move the appointment. Your original appointment is unchanged."
    )


def _slot_refusal(result: dict, lang: str, asked: str | None = None) -> str | None:
    """See reply_templates._slot_refusal_bn: valid_slots are schedule start times, said as a range, never as free.
    `asked`: the time the caller gave, so a time inside the range but off the clinic's grid is answered with the
    two start times either side of it. (Hindi and English wording is REASONED, not reviewed by a native speaker.)"""
    from agent.reply_templates import off_grid_neighbours

    hi = lang == "hi"
    reason = result.get("reason")
    valid = result.get("valid_slots") or []
    if reason == "invalid_slot":
        near = off_grid_neighbours(asked, valid)
        if near:
            below, above, step = near
            if hi:
                every = f" समय हर {step} मिनट पर होते हैं।" if step else ""
                return f"माफ़ कीजिए, {asked} पर अपॉइंटमेंट नहीं होती।{every} {below} या {above}, कौन सा ठीक रहेगा?"
            every = f" Times are every {step} minutes." if step else ""
            return f"Sorry, appointments aren't given at {asked}.{every} {below} or {above}, which would suit you?"
        if valid:
            return (
                f"माफ़ कीजिए, उस समय अपॉइंटमेंट नहीं होती। {valid[0]} से {valid[-1]} के बीच कोई समय बताएँगे?"
                if hi
                else f"Sorry, appointments aren't given at that time. Could you give a time between {valid[0]} and {valid[-1]}?"
            )
        return (
            "माफ़ कीजिए, वह समय ठीक नहीं है। चेंबर के समय के भीतर कोई समय बताएँगे?"
            if hi
            else "Sorry, that time isn't valid. Could you give a time within the chamber hours?"
        )
    if reason == "slot_passed":
        if valid:
            return (
                f"वह समय आज निकल चुका है। आज {valid[0]} से {valid[-1]} के बीच कोई समय बताएँगे, या कोई और दिन?"
                if hi
                else f"That time has already passed today. Could you give a time between {valid[0]} and {valid[-1]} today, or another day?"
            )
        return (
            "वह समय आज निकल चुका है। कोई और दिन बताएँगे?"
            if hi
            else "That time has already passed today. Could you say another day?"
        )
    if reason == "sitting_over":
        return (
            "आज का चैंबर का समय निकल चुका है। कोई और दिन बताएँगे?"
            if hi
            else "Today's chamber is already over. Could you say another day?"
        )
    return None


# story: "Caller cancels an appointment"
def refund_sentence(result: dict, lang: str) -> str:
    """Refund ELIGIBILITY in words, never an amount -- see reply_templates.refund_sentence."""
    hi = lang == "hi"
    eligibility = result.get("refund_eligibility")
    if eligibility == "full":
        return "नियम के अनुसार आप पूरे रिफ़ंड के हक़दार हैं।" if hi else "Under the policy you are eligible for a full refund."
    if eligibility == "partial" and result.get("refund_percent"):
        percent = result["refund_percent"]
        return (
            f"नियम के अनुसार आप {percent} प्रतिशत रिफ़ंड के हक़दार हैं।"
            if hi
            else f"Under the policy you are eligible for a {percent} percent refund."
        )
    if eligibility == "none":
        return "नियम के अनुसार इस पर कोई रिफ़ंड नहीं मिलेगा।" if hi else "Under the policy no refund applies in this case."
    return ""


def _with_refund(line: str, result: dict, lang: str) -> str:
    refund = refund_sentence(result, lang)
    return f"{line} {refund}" if refund else line


def cancel_reply(result: dict, lang: str) -> str:
    hi = lang == "hi"
    reason = result.get("reason")
    if reason == "charge_confirmation_required":
        charge = result["charge_inr"]
        stated = _with_refund(
            f"इस समय रद्द करने पर {charge} रुपये कटेंगे।"
            if hi
            else f"Cancelling now means a charge of {charge} rupees.",
            result,
            lang,
        )
        return f"{stated} फिर भी रद्द कर दूँ?" if hi else f"{stated} Shall I still cancel it?"
    if result.get("success"):
        charge = result.get("charge_inr") or 0
        if charge:
            return _with_refund(
                f"आपकी अपॉइंटमेंट रद्द कर दी गई है। {charge} रुपये काटे गए हैं।"
                if hi
                else f"Your appointment has been cancelled. A charge of {charge} rupees has been applied.",
                result,
                lang,
            )
        return _with_refund(
            "आपकी अपॉइंटमेंट बिना किसी शुल्क के रद्द कर दी गई है।"
            if hi
            else "Your appointment has been cancelled with no charge.",
            result,
            lang,
        )
    if reason == "not_found":
        return (
            "माफ़ कीजिए, इस कन्फ़र्मेशन नंबर पर कोई अपॉइंटमेंट नहीं मिली।"
            if hi
            else "Sorry, I couldn't find an appointment with that confirmation number."
        )
    return (
        "माफ़ कीजिए, रद्द नहीं हो सकी। काउंटर से संपर्क करें।"
        if hi
        else "Sorry, I couldn't cancel it. Please contact the counter."
    )


# story: "Caller asks what they have booked"
def lookup_reply(bookings: list[dict], lang: str) -> str:
    """See reply_templates.lookup_reply: the next upcoming booking, the doctor OR the test, and
    the time only when the record actually carries one."""
    from agent.reply_templates import booked_subject

    hi = lang == "hi"
    if not bookings:
        return (
            "माफ़ कीजिए, आपके नाम कोई आने वाली बुकिंग नहीं मिली।"
            if hi
            else "Sorry, I couldn't find any upcoming booking for you."
        )
    b = bookings[0]
    subject = booked_subject(b, lang)
    day = spoken_day(b.get("date"), lang) or b.get("date")
    if hi:
        when = f"{day} को, समय {b['time_slot']}" if b.get("time_slot") else f"{day} को"
        return (
            f"आपकी अगली बुकिंग {subject}, {when}। "
            f"कन्फ़र्मेशन नंबर {b['confirmation_id']}। "
            f"लिखित कन्फ़र्मेशन फिर से भेज दूँ?"
        )
    when = f"on {day} at {b['time_slot']}" if b.get("time_slot") else f"on {day}"
    return (
        f"Your next booking is {subject}, {when}. "
        f"The confirmation number is {b['confirmation_id']}. "
        f"Shall I resend the written confirmation?"
    )


def multiple_bookings_reply(bookings: list[dict], lang: str) -> str:
    hi = lang == "hi"
    parts = (
        [f"{b.get('doctor_name')} डॉक्टर के पास {b['date']} की" for b in bookings[:3]]
        if hi
        else [f"with {b.get('doctor_name')} on {b['date']}" for b in bookings[:3]]
    )
    if hi:
        return f"आपके नाम कई बुकिंग हैं -- {', '.join(parts)}। कौन सी बुकिंग, कन्फ़र्मेशन नंबर बताइए?"
    return (
        f"You have more than one booking -- {', '.join(parts)}. Which one -- could you give me the confirmation number?"
    )


def multi_test_reply(result: dict, lang: str) -> str:
    hi = lang == "hi"
    if not result.get("success"):
        return (
            "माफ़ कीजिए, टेस्ट बुक नहीं हो सके। थोड़ी देर बाद फिर कोशिश करें।"
            if hi
            else "Sorry, I couldn't book the tests. Please try again shortly."
        )
    names = ", ".join(result["test_names"])
    if hi:
        reply = (
            f"{names} -- ये टेस्ट {result['date']} को बुक कर दिए गए हैं। "
            f"कुल खर्च {result['total_rate_inr']} रुपये। कन्फ़र्मेशन नंबर: {result['confirmation_id']}।"
        )
        if result.get("combined_prep"):
            reply += f" तैयारी: {result['combined_prep']}"
        return reply
    reply = (
        f"{names} have been booked for {result['date']}. Total cost {result['total_rate_inr']} rupees. "
        f"Confirmation number: {result['confirmation_id']}."
    )
    if result.get("combined_prep"):
        reply += f" Preparation: {result['combined_prep']}"
    return reply


def add_test_reply(result: dict, lang: str) -> str:
    hi = lang == "hi"
    if result.get("success"):
        return (
            f"{result['test_name']} टेस्ट आपकी {result['date']} की बुकिंग में जोड़ दिया गया है।"
            if hi
            else f"The {result['test_name']} test has been added to your booking for {result['date']}."
        )
    reason = result.get("reason")
    if reason == "not_found":
        return (
            "माफ़ कीजिए, इस कन्फ़र्मेशन नंबर पर कोई टेस्ट बुकिंग नहीं मिली।"
            if hi
            else "Sorry, I couldn't find a test booking with that confirmation number."
        )
    if reason == "test_not_found":
        return "माफ़ कीजिए, यह टेस्ट हमारी सूची में नहीं है।" if hi else "Sorry, that test is not on our list."
    if reason == "already_booked":
        return "यह टेस्ट तो पहले से ही आपकी बुकिंग में है।" if hi else "That test is already part of your booking."
    return "माफ़ कीजिए, टेस्ट जोड़ा नहीं जा सका।" if hi else "Sorry, I couldn't add that test."


def resend_reply(result: dict, lang: str) -> str:
    hi = lang == "hi"
    if result.get("success"):
        return (
            f"कन्फ़र्मेशन दोबारा भेज दिया गया है, नंबर खत्म होता है {result['sent_to_last4']} से।"
            if hi
            else f"The confirmation has been resent, to the number ending in {result['sent_to_last4']}."
        )
    if result.get("reason") == "rate_limited":
        return (
            "अभी थोड़ी देर पहले ही भेजा गया था। थोड़ी देर बाद फिर कहिए।"
            if hi
            else "It was already sent a moment ago. Please ask again shortly."
        )
    if result.get("reason") == "no_phone_on_file":
        return (
            "माफ़ कीजिए, इस बुकिंग के लिए कोई फ़ोन नंबर दर्ज नहीं है, इसलिए भेज नहीं पा रही।"
            if hi
            else "Sorry, there's no phone number on file for this booking, so I can't send it."
        )
    return (
        "माफ़ कीजिए, इस कन्फ़र्मेशन नंबर पर कोई बुकिंग नहीं मिली।"
        if hi
        else "Sorry, I couldn't find a booking with that confirmation number."
    )


_NAMES_PER_GROUP = 5


def blood_test_list_reply(spoken_names: list[str], lang: str) -> str:
    """`spoken_names` are already the short codes (reply_templates.blood_test_list_reply calls
    agent.catalogue_forms.lab_test_code before delegating here).

    Grouped into short runs, each ending in a sentence break -- same fix, same reason, as the Bengali version:
    one long comma-separated run with no internal sentence break became one over-long TTS clause on a live call
    (2026-09-28) and was never actually spoken."""
    hi = lang == "hi"
    if not spoken_names:
        return (
            "माफ़ कीजिए, अभी हमारी सूची में कोई ब्लड टेस्ट नहीं है। काउंटर पर पूछ सकते हैं।"
            if hi
            else "Sorry, we don't have any blood tests listed right now. You could ask at the counter."
        )
    groups = [spoken_names[i : i + _NAMES_PER_GROUP] for i in range(0, len(spoken_names), _NAMES_PER_GROUP)]
    if hi:
        listed = "। ".join(", ".join(g) for g in groups)
        return f"हमारे यहाँ ये ब्लड टेस्ट होते हैं -- {listed}। इनमें से कौन सा करवाना है?"
    listed = ". ".join(", ".join(g) for g in groups)
    return f"These are the blood tests we do -- {listed}. Which one would you like?"


def department_route_reply(result: dict, symptom: str, lang: str) -> str:
    from agent.reply_templates import spoken_department

    hi = lang == "hi"
    if result.get("matched"):
        doctors = result.get("doctors") or []
        who = (
            (f" हमारे यहाँ {', '.join(doctors)} हैं।" if hi else f" We have {', '.join(doctors)} here.") if doctors else ""
        )
        dept = spoken_department(result, lang, "department_name")
        base = (
            f"इसके लिए {dept} विभाग में दिखाना ठीक रहेगा।"
            if hi
            else f"For this, it would be best to see the {dept} department."
        )
        ask = " डॉक्टर की अपॉइंटमेंट बना दूँ?" if hi else " Shall I book a doctor's appointment?"
        return base + who + ask
    if result.get("ambiguous"):
        by_dept = result.get(f"doctors_by_department_{lang}") or result.get("doctors_by_department") or {}

        def _with_doctors(name: str) -> str:
            docs = by_dept.get(name) or []
            return f"{name} ({', '.join(docs)})" if docs else name

        parts = [_with_doctors(n) for n in (result.get(f"candidates_{lang}") or result["candidates"])]
        return (
            f"यह {' या '.join(parts)} -- इनमें से कोई एक विभाग हो सकता है। कौन सा बताएँगे?"
            if hi
            else f"This could be either {' or '.join(parts)}. Which would you like?"
        )
    return (
        "माफ़ कीजिए, ठीक से समझ नहीं पाई किस विभाग में दिखाना होगा। काउंटर पर पूछ सकते हैं।"
        if hi
        else "Sorry, I couldn't quite tell which department this needs. You could ask at the counter."
    )


def conflict_reply(conflict: dict, lang: str) -> str:
    hi = lang == "hi"
    if hi:
        return (
            f"उस समय आपकी पहले से एक अपॉइंटमेंट है -- {conflict.get('doctor_name')} डॉक्टर के पास, "
            f"{conflict['date']} को, समय {conflict['time_slot']} पर। पुरानी रखूँ, बदलूँ, या नई जोड़ दूँ?"
        )
    return (
        f"You already have an appointment at that time -- with {conflict.get('doctor_name')}, "
        f"on {conflict['date']} at {conflict['time_slot']}. Shall I keep it, move it, or add a new one?"
    )


# story: "Caller asks for the earliest available appointment"
def earliest_available_reply(result: dict, lang: str) -> str:
    hi = lang == "hi"
    if not result.get("found"):
        q = result.get("query")
        return f"माफ़ कीजिए, '{q}' नाम के कोई डॉक्टर नहीं मिले।" if hi else f"Sorry, I couldn't find a doctor named '{q}'."
    if not result.get("available"):
        return (
            f"माफ़ कीजिए, {result.get('doctor_name')} डॉक्टर के पास नज़दीकी समय में कोई जगह खाली नहीं है। "
            f"बाद में फिर फ़ोन करें तो बता पाऊँगी।"
            if hi
            else f"Sorry, {result.get('doctor_name')} has nothing free in the near term. "
            f"Please call again later and I can check for you."
        )
    if hi:
        reply = f"सबसे जल्दी खाली है {result['date']} को, समय {result['time_slot']} पर।"
        alts = result.get("alternatives") or []
        if alts:
            reply += f" इसके अलावा: {', '.join(alts)}।"
        return reply
    reply = f"The earliest opening is on {result['date']} at {result['time_slot']}."
    alts = result.get("alternatives") or []
    if alts:
        reply += f" Also available: {', '.join(alts)}."
    return reply


def draft_resume_reply(draft: dict, lang: str) -> str:
    hi = lang == "hi"
    return (
        "पिछली बार कॉल कट गई थी, आपकी बुकिंग पूरी नहीं हुई थी। जहाँ से छोड़ा था वहीं से जारी रखूँ, या नए सिरे से शुरू करूँ?"
        if hi
        else "Your call dropped last time before the booking finished. Shall I continue where we left off, or start fresh?"
    )


# story: "Patient name is misheard"
def spelling_prompt(lang: str) -> str:
    hi = lang == "hi"
    return "नाम एक-एक अक्षर करके बताएँगे?" if hi else "Could you spell the name out for me, letter by letter?"


def spelling_readback(spelled: str, lang: str) -> str:
    hi = lang == "hi"
    return f"मैंने सुना {spelled.upper()} -- क्या यह ठीक है?" if hi else f"I heard {spelled.upper()} -- is that right?"


# ============================================== ported story: "Caller asks which doctors are available"
# Same discipline as the Bengali originals in reply_templates.py: every name is
# a column from the verified API response, in the script this language's voice
# can say, and a long list is grouped into short runs so TTS gets a sentence
# break (one long comma run was never actually spoken on a live call).


def doctor_chosen_ack(slots: dict, lang: str) -> str:
    """Hindi / English twin of agent/reply_templates.doctor_chosen_ack."""
    if lang == "hi":
        return f"ठीक है, {slots.get('doctor_name_hi') or slots.get('doctor_name')} डॉक्टर।"
    return f"All right, {_with_title(slots.get('doctor_name'))}."


def doctor_choice_prompt(departments: list[str], lang: str) -> str:
    """Hindi / English twin of agent/reply_templates.doctor_choice_prompt (see its docstring)."""
    from agent.reply_templates import _grouped

    if lang == "hi":
        ask = "किस डॉक्टर को दिखाना चाहते हैं? नाम न पता हो तो तकलीफ़ या विभाग बताइए।"
        if not departments:
            return ask
        listed = "। ".join(", ".join(g) for g in _grouped(departments))
        return f"{ask} हमारे यहाँ ये विभाग हैं -- {listed}।"
    ask = "Which doctor would you like to see? If you don't know a name, tell me the problem or the department."
    if not departments:
        return ask
    listed = ". ".join(", ".join(g) for g in _grouped(departments))
    return f"{ask} We have {listed}."


def department_list_prompt(departments: list[str], lang: str) -> str:
    """Hindi / English twin of agent/reply_templates.department_list_prompt (see its docstring)."""
    from agent.reply_templates import _grouped

    if lang == "hi":
        if not departments:
            return "क्या तकलीफ़ हो रही है, ज़रा बताइए? उसी के हिसाब से बता दूँगी किस विभाग में दिखाना है।"
        listed = "। ".join(", ".join(g) for g in _grouped(departments))
        return f"हमारे यहाँ ये विभाग हैं -- {listed}। किस विभाग में दिखाना चाहते हैं? तकलीफ़ बताएँगे तो सही विभाग भी बता दूँगी।"
    if not departments:
        return "Could you tell me what the problem is? I'll tell you which department to see."
    listed = ". ".join(", ".join(g) for g in _grouped(departments))
    return f"We have these departments: {listed}. Which one would you like? Or tell me the problem and I'll find the right one."


def doctor_list_reply(result: dict, lang: str) -> str:
    from agent.reply_templates import (
        MAX_DOCTORS_SPOKEN,
        _grouped,
        _spoken_doctor,
        spoken_departments,
    )

    hi = lang == "hi"
    names = [n for n in (_spoken_doctor(d, lang) for d in (result.get("doctors") or [])) if n]
    if not result.get("found") or not names:
        return (
            "माफ़ कीजिए, अभी डॉक्टरों की सूची नहीं देख पा रही। काउंटर पर पूछ सकते हैं।"
            if hi
            else "Sorry, I cannot see the list of doctors just now. You could ask at the counter."
        )
    if len(names) > MAX_DOCTORS_SPOKEN:
        # Hindi needs the spoken form or the whole reply is blocked; English
        # wants the Latin name, which is what spoken_departments falls back to.
        departments = spoken_departments(result["doctors"], lang)
        if hi:
            listed = "। ".join(", ".join(g) for g in _grouped(departments))
            return f"हमारे यहाँ इन विभागों में डॉक्टर बैठते हैं -- {listed}। आप किस विभाग के बारे में पूछ रहे हैं?"
        listed = ". ".join(", ".join(g) for g in _grouped(departments))
        return f"We have doctors in these departments -- {listed}. Which department did you mean?"
    if hi:
        listed = "। ".join(", ".join(g) for g in _grouped(names))
        return f"हमारे यहाँ ये डॉक्टर बैठते हैं -- {listed}। किनके साथ अपॉइंटमेंट लेना चाहेंगे?"
    listed = ". ".join(", ".join(g) for g in _grouped(names))
    return f"These are the doctors we have -- {listed}. Who would you like an appointment with?"


def doctors_by_department_reply(result: dict, lang: str) -> str:
    from agent.reply_templates import (
        _grouped,
        _spoken_doctor,
        spoken_department,
    )

    hi = lang == "hi"
    if not result.get("found"):
        offered = result.get(f"did_you_mean_{lang}") or result.get("did_you_mean") or []
        query = result.get("query") or ""
        if result.get("ambiguous") and offered:
            return (
                f"कई विभाग मिले -- आप {' या '.join(offered)}, किसकी बात कर रहे हैं?"
                if hi
                else f"I found more than one department -- did you mean {' or '.join(offered)}?"
            )
        if offered:
            return (
                f"'{query}' नाम का विभाग नहीं मिला। क्या आपका मतलब {', '.join(offered)} है?"
                if hi
                else f"I could not find a department called '{query}'. Did you mean {', '.join(offered)}?"
            )
        return (
            f"माफ़ कीजिए, '{query}' नाम का कोई विभाग हमारी सूची में नहीं है। किसी और विभाग या डॉक्टर का नाम बताएँगे?"
            if hi
            else f"Sorry, there is no department called '{query}' on our list. "
            "Could you give me another department or a doctor's name?"
        )
    names = [n for n in (_spoken_doctor(d, lang) for d in (result.get("doctors") or [])) if n]
    department = spoken_department(result, lang)
    if not names:
        return (
            f"{department} विभाग में अभी कोई डॉक्टर सूची में नहीं है। किसी और विभाग के बारे में देखूँ?"
            if hi
            else f"There are no doctors listed in {department} just now. Shall I look at another department?"
        )
    if hi:
        listed = "। ".join(", ".join(g) for g in _grouped(names))
        return f"{department} विभाग में ये डॉक्टर बैठते हैं -- {listed}। किनके साथ अपॉइंटमेंट लेना चाहेंगे?"
    listed = ". ".join(", ".join(g) for g in _grouped(names))
    return f"These are the doctors in {department} -- {listed}. Who would you like an appointment with?"


# ============================================== ported story: "Caller asks to compare two options"
# Prices only -- see the Bengali original in reply_templates.py for why nothing
# here ranks the two options on anything but arithmetic.


def compare_options_reply(cmp: dict, name_a: str, name_b: str, lang: str) -> str:
    hi = lang == "hi"
    if not cmp.get("a_found") or not cmp.get("b_found"):
        missing = name_a if not cmp.get("a_found") else name_b
        return (
            f"'{missing}' हमारी सूची में नहीं मिला, इसलिए तुलना नहीं कर पा रही। दोनों नाम फिर से बताएँगे?"
            if hi
            else f"I could not find '{missing}' on our list, so I cannot compare them. Could you give me both names again?"
        )
    if cmp.get("price_delta") is None:
        return (
            f"{name_a} और {name_b} -- दोनों की कीमत अभी नहीं देख पा रही। काउंटर पर पूछ सकते हैं।"
            if hi
            else f"I cannot see the price of {name_a} and {name_b} just now. You could ask at the counter."
        )
    delta = cmp["price_delta"]
    if cmp.get("cheaper") is None:
        return (
            f"{name_a} और {name_b} -- दोनों की कीमत एक ही है। कौन सा करवाना है?"
            if hi
            else f"{name_a} and {name_b} cost the same. Which would you like?"
        )
    cheaper = name_a if cmp["cheaper"] == "a" else name_b
    dearer = name_b if cmp["cheaper"] == "a" else name_a
    if hi:
        line = f"{cheaper} {dearer} से {delta} रुपये सस्ता है। "
        if cmp.get("both_packages") and cmp.get("more_tests_side"):
            more = name_a if cmp["more_tests_side"] == "a" else name_b
            line += f"{more} में {cmp['test_count_delta']} टेस्ट ज़्यादा हैं। "
        return line + "कौन सा करवाना है?"
    line = f"{cheaper} is {delta} rupees cheaper than {dearer}. "
    if cmp.get("both_packages") and cmp.get("more_tests_side"):
        more = name_a if cmp["more_tests_side"] == "a" else name_b
        line += f"{more} includes {cmp['test_count_delta']} more tests. "
    return line + "Which would you like?"


# ============================================== KCD-387 full flow: "Caller asks whether a test can
# be collected at home" -- Hindi/English counterparts of reply_templates.py's Bengali originals.
# Same discipline: every number here is a straight substitution from a real clinic-api response.


def home_collection_eligibility_reply(result: dict, lang: str) -> str:
    hi = lang == "hi"
    rows = result.get("results") or []
    not_found = result.get("not_found") or []
    eligible = [r for r in rows if r.get("found") and r.get("eligible")]
    ineligible = [r for r in rows if r.get("found") and not r.get("eligible")]
    parts = []
    if eligible:
        names = ", ".join(r.get("test_name") or "" for r in eligible)
        parts.append(
            f"{names} -- इन टेस्ट(ओं) के लिए घर पर सैंपल लिया जा सकता है।"
            if hi
            else f"{names} -- home collection is available for this/these test(s)."
        )
    for r in ineligible:
        name = r.get("test_name") or ""
        if r.get("reason") == "area_not_covered":
            parts.append(
                f"{name} के लिए आपके इलाके में अभी घर पर सैंपल लेने की सुविधा नहीं है।"
                if hi
                else f"Home collection is not yet available in your area for {name}."
            )
        else:
            parts.append(
                f"{name} टेस्ट घर पर नहीं किया जा सकता, लैब में ही आना होगा।"
                if hi
                else f"{name} cannot be collected at home -- it needs a lab visit."
            )
    for name in not_found:
        parts.append(f"'{name}' नाम का कोई टेस्ट नहीं मिला।" if hi else f"I could not find a test called '{name}'.")
    if not parts:
        parts.append(
            "माफ़ कीजिए, अभी इस टेस्ट की जानकारी नहीं दे पा रही।" if hi else "Sorry, I can't check that test right now."
        )
    return " ".join(parts)


def home_collection_slots_reply(slots: list[dict], lang: str) -> str:
    hi = lang == "hi"
    if not slots:
        return "माफ़ कीजिए, उस दिन कोई स्लॉट खाली नहीं है। कोई और दिन बताएँगे?" if hi else "Sorry, there's no slot free that day. Could you try another day?"
    shown = slots[:MAX_SLOTS_READ_ALOUD]
    if len(shown) == 1:
        s = shown[0]
        return (
            f"{s['date']} को {s['start_time']} से {s['end_time']} के बीच स्लॉट खाली है। यही रखूँ?"
            if hi
            else f"There's a slot free on {s['date']} between {s['start_time']} and {s['end_time']}. Shall I hold that one?"
        )
    windows = ", ".join(f"{s['start_time']} से {s['end_time']}" if hi else f"{s['start_time']} to {s['end_time']}" for s in shown)
    return (
        f"{shown[0]['date']} को ये समय खाली हैं -- {windows}। कौन सा चाहिए?"
        if hi
        else f"These windows are free on {shown[0]['date']} -- {windows}. Which would you like?"
    )


def home_collection_hold_failed_reply(result: dict, lang: str) -> str:
    hi = lang == "hi"
    if result.get("reason") == "slot_full":
        return "माफ़ कीजिए, वह समय अभी भर गया। कोई और समय बताएँगे?" if hi else "Sorry, that window just filled up. Could you pick another?"
    return "माफ़ कीजिए, समय रोक नहीं पाई। फिर कोशिश करूँ?" if hi else "Sorry, I couldn't hold that slot. Shall I try again?"


def home_collection_quote_reply(quote: dict, payment_policy_text: str | None, lang: str) -> str:
    hi = lang == "hi"
    if not quote.get("quote_available"):
        return (
            "माफ़ कीजिए, इस टेस्ट की कीमत अभी नहीं बता पा रही।" if hi else "Sorry, I can't work out the price for this right now."
        )
    lines = [f"{r.get('test_name')} {r.get('rate_inr')}" for r in (quote.get("per_test") or []) if r.get("eligible")]
    breakdown = ", ".join(lines)
    charge = quote.get("home_collection_charge_inr") or 0
    total = quote.get("total_inr") or 0
    if hi:
        reply = f"{breakdown} रुपये, और घर पर सैंपल लेने के लिए {charge} रुपये -- कुल मिलाकर {total} रुपये।"
    else:
        reply = f"{breakdown} rupees, plus {charge} rupees for home collection -- {total} rupees in total."
    if payment_policy_text:
        reply += f" {payment_policy_text}"
    return reply + (" आगे बढ़ूँ?" if hi else " Shall I go ahead?")


def home_collection_stored_address_offer(address: str, lang: str) -> str:
    hi = lang == "hi"
    return (
        f"पिछली बार दिया गया यही पता भेजूँ -- {address}? या नया पता देंगे?"
        if hi
        else f"Shall I use the address you gave last time -- {address}? Or would you like to give a new one?"
    )


def home_collection_address_pincode_mismatch_reply(address_pin: str, given_pin: str, lang: str) -> str:
    hi = lang == "hi"
    if hi:
        return f"आपके पते में {address_pin} पिनकोड सुना, लेकिन पहले आपने {given_pin} बताया था -- दोनों अलग हैं। पता फिर से बताएँगे?"
    return f"I heard pincode {address_pin} in your address, but you earlier gave {given_pin} -- those are different. Could you give me the address again?"


def home_collection_address_confirm_reply(address: str, lang: str) -> str:
    hi = lang == "hi"
    return f"पता सुना -- {address}। यह सही है?" if hi else f"I heard the address as -- {address}. Is that correct?"


def home_collection_final_confirm_reply(summary: dict, lang: str) -> str:
    # ADDED BY SOURAV: the hi readback is split across 4 short sentences, not 1 long run-on --
    # persona.violations()'s own per-language MAX_SENTENCE_WORDS cap (hi: 18) exists so a caller
    # hears this read-back in digestible pieces, not a single breathless clause.
    hi = lang == "hi"
    tests = ", ".join(summary.get("eligible_tests") or [])
    if hi:
        return (
            f"तो {tests} -- {summary.get('date')} को, समय {summary.get('start_time')} से "
            f"{summary.get('end_time')} के बीच घर पर सैंपल लिया जाएगा। "
            f"पता -- {summary.get('address')}। "
            f"कुल {summary.get('total_inr')} रुपये। यह कन्फ़र्म कर दूँ?"
        )
    return (
        f"So {tests} -- on {summary.get('date')}, between {summary.get('start_time')} and "
        f"{summary.get('end_time')}, at {summary.get('address')} -- home collection, total "
        f"{summary.get('total_inr')} rupees. Shall I confirm this?"
    )


def home_collection_booking_result_reply(result: dict, lang: str) -> str:
    hi = lang == "hi"
    if not result.get("success"):
        reason = result.get("reason")
        if reason == "hold_expired":
            return (
                "माफ़ कीजिए, समय रोक नहीं पाई, थोड़ी देर हो गई। फिर कोशिश करूँ?"
                if hi
                else "Sorry, the slot couldn't be held in time. Shall I try again?"
            )
        if reason in ("no_eligible_test", "test_not_found"):
            return (
                "माफ़ कीजिए, इस टेस्ट के लिए घर पर बुकिंग नहीं हो पाई।" if hi else "Sorry, I couldn't book home collection for this test."
            )
        return (
            "माफ़ कीजिए, बुकिंग नहीं हो पाई। थोड़ी देर बाद फिर कोशिश करें, या काउंटर पर संपर्क करें।"
            if hi
            else "Sorry, the booking couldn't go through. Please try again shortly, or contact the counter."
        )
    collector = result.get("collector_name")
    if hi:
        assignment_line = f" {collector} आएँगी/आएँगे।" if collector else " कौन आएगा, यह हमारा स्टाफ़ बाद में कन्फ़र्म करेगा।"
        return (
            f"आपकी घर पर सैंपल लेने की बुकिंग कन्फ़र्म हो गई है। "
            f"{result.get('date')} को, समय {result.get('start_time')} से {result.get('end_time')} के बीच, "
            f"पता {result.get('address_line')} पर।{assignment_line} "
            f"कुल {result.get('total_inr')} रुपये। कन्फ़र्मेशन नंबर {result.get('booking_reference')}।"
        )
    assignment_line = f" {collector} will come." if collector else " Our staff will confirm who will come."
    return (
        f"Your home collection booking is confirmed. On {result.get('date')}, between "
        f"{result.get('start_time')} and {result.get('end_time')}, at {result.get('address_line')}."
        f"{assignment_line} Total {result.get('total_inr')} rupees. Confirmation number "
        f"{result.get('booking_reference')}."
    )


def home_collection_cancelled_reply(lang: str) -> str:
    hi = lang == "hi"
    return "ठीक है, बुकिंग नहीं की गई। कुछ और मदद चाहिए?" if hi else "Okay, no booking was made. Is there anything else I can help with?"


# ---------------------------------------------------------------------------- KCD-382 / KCD-382b
# Restored during the multilingual merge: these two templates live on the Sourav branch only, and the
# multilingual snapshot predated them, so a plain 3-way merge dropped them. Behaviour unchanged.
def merged_test_prep_reply(test_names: list[str], result: dict, lang: str) -> str:
    """Hindi/English counterpart of reply_templates.merged_test_prep_reply -- see that function's docstring
    (KCD-382, "several tests with conflicting preparation"). Same rule as everywhere else in this file: every
    fact is a substitution from `result`, never composed here."""
    hi = lang == "hi"
    not_found = result.get("not_found") or []
    if not result.get("found") or not_found:
        missing = ", ".join(not_found) if not_found else ", ".join(test_names)
        if hi:
            return f"माफ़ कीजिए, {missing} -- इस नाम का टेस्ट हमारी सूची में नहीं मिला। क्या आप नाम फिर से बताएँगे?"
        return f"Sorry, I couldn't find {missing} on our list. Could you say the test names again?"

    names = ", ".join(result.get("test_names") or test_names)
    hours = result.get("fasting_hours")
    if hours:
        if hi:
            water = "सिर्फ़ पानी पी सकते हैं।" if result.get("water_allowed_while_fasting") else "पानी भी नहीं पी सकते।"
            instruction = f"सबसे सख़्त नियम माननी होगी -- कम से कम {hours} घंटे खाली पेट रहना होगा, {water}"
        else:
            water = "Only water is allowed." if result.get("water_allowed_while_fasting") else "Not even water is allowed."
            instruction = f"The strictest rule applies -- fast for at least {hours} hours. {water}"
    else:
        instruction = (
            "इन टेस्ट के लिए अलग से खाली पेट रहने की ज़रूरत नहीं है।"
            if hi
            else "No fasting is needed for these tests."
        )
    if result.get("prescription_required"):
        instruction += (
            " इनमें से कम से कम एक टेस्ट के लिए डॉक्टर का पर्चा ज़रूरी है।"
            if hi
            else " At least one of these tests needs a doctor's prescription."
        )
    return f"{names} टेस्ट के लिए, {instruction}" if hi else f"For {names}, {instruction}"


def multiple_test_bookings_reply(bookings: list[dict], lang: str) -> str:
    """KCD-382b: see agent.reply_templates.multiple_test_bookings_reply's own docstring (the bn
    primary this function is the hi/en fallback for)."""
    hi = lang == "hi"
    parts = (
        [f"{b.get('test_name')}, {b['date']} की" for b in bookings[:3]]
        if hi
        else [f"{b.get('test_name')} on {b['date']}" for b in bookings[:3]]
    )
    if hi:
        return f"आपके नाम कई टेस्ट बुकिंग हैं -- {', '.join(parts)}। कौन सी बुकिंग, कन्फ़र्मेशन नंबर बताइए?"
    return (
        f"You have more than one test booking -- {', '.join(parts)}. "
        f"Which one -- could you give me the confirmation number?"
    )
