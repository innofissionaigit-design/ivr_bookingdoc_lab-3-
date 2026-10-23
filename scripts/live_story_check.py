"""Live story check: every booking / doctor / lab story, spoken in Bengali, Hindi and English, through the REAL stack.

Run ON THE POD (main venv), with the stack up (deploy/vast/start_vast.sh):

    /workspace/venv/bin/python scripts/live_story_check.py --port 10100 --out /workspace/logs/live_stories.json
    /workspace/venv/bin/python scripts/live_story_check.py --only S17 --langs bn          # one story, one language

For each scenario and language it synthesises the caller's words with the live TTS (tts_server.py on :8002), streams them
to /ws/audio exactly as a phone would (hello frame, 16 kHz PCM, playback_done), and records what the agent HEARD and
what it SAID, turn by turn. The caller is not a fixed script: it has a persona (doctor, day, time, patient, phone) and
answers whatever the agent actually asked -- so a flow that asks in a different order is still followed to the end,
and a flow that never gets there shows where it stopped.

Every reply is checked for two things:
  * the story's own expectation (a price, a weekday, "the counter", a confirmation, ...);
  * LANGUAGE: the reply is in the language the caller spoke (by script share). A Bengali caller answered in Hindi is
    a failure even when the content is right -- that is the defect this check exists for.

What this does NOT prove: recognition on real telephony audio. The caller here is clean synthetic speech from this
stack's own TTS (scripts/e2e_call.py says the same). It proves that each flow is REACHABLE in each language and
behaves as the story says once reached.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_call import load_16k_mono, stream  # noqa: E402  (same streaming the browser uses)

TTS_URL = "http://127.0.0.1:8002/synthesize"
QUIET_S = 4.0  # a reply is over when nothing new has arrived for this long (fillers come first, answers later)
MAX_TURNS = 9

# ---------------------------------------------------------------------------------------------------------------------
# Personas: what each caller knows, per language. Numbers are said as words, the way a caller says them.
# ---------------------------------------------------------------------------------------------------------------------
# A fuller yes: a lone one-syllable "हाँ" came back as an EMPTY transcript on the live run (2026-10-07) -- a finding
# recorded in the story report, not something to hide by retrying.
YES = {"bn": "হ্যাঁ, ঠিক আছে", "hi": "हाँ जी, ठीक है", "en": "yes, that is right"}
NO_NUMBER = {"bn": "আমার কাছে কোনো নম্বর নেই", "hi": "मेरे पास कोई नंबर नहीं है", "en": "I do not have a number"}
BOOK_IT = {
    "bn": "ওই দিন অ্যাপয়েন্টমেন্টটা বুক করে দিন",
    "hi": "उसी दिन का अपॉइंटमेंट बुक कर दीजिए",
    "en": "please book the appointment for that day",
}
NO_MORE = {"bn": "না, আর কিছু না, ধন্যবাদ", "hi": "नहीं, बस इतना ही, धन्यवाद", "en": "no, that's all, thank you"}
PHONES = {
    "bn": "নয় আট তিন শূন্য শূন্য এক দুই তিন চার পাঁচ",
    "hi": "नौ आठ तीन शून्य शून्य पांच छह सात आठ नौ",
    "en": "nine eight three zero zero nine eight seven six five",
}
PHONE_DIGITS = {"bn": "9830012345", "hi": "9830056789", "en": "9830098765"}
# Every OTHER booking uses these, so the lookup / move / cancel scenarios (keyed on PHONES) find exactly one booking.
OTHER_PHONES = {
    "bn": "নয় আট তিন এক এক এক দুই দুই দুই তিন",
    "hi": "नौ आठ तीन एक एक एक दो दो दो तीन",
    "en": "nine eight three one one one two two two three",
}
TEST_PHONES = ("9830012345", "9830056789", "9830098765", "9831112223")


def persona(lang: str, doctor: dict, day: dict, time_: dict, name: dict, phone: str | None = None) -> dict:
    return {
        "doctor": doctor[lang],
        "day": day[lang],
        "time": time_[lang],
        "name": name[lang],
        "phone": OTHER_PHONES[lang] if phone is None else phone,
    }


DAS = {"bn": "ডাক্তার দেবাশিস দাস", "hi": "डॉक्टर देबाशीष दास", "en": "Doctor Debasish Das"}
SEN = {"bn": "ডাক্তার অরিন্দম সেন", "hi": "डॉक्टर अरिंदम सेन", "en": "Doctor Arindam Sen"}
ROY = {"bn": "ডাক্তার নির্মল রায়", "hi": "डॉक्टर निर्मल रॉय", "en": "Doctor Nirmal Roy"}
MUKHERJEE = {"bn": "ডাক্তার সৌরভ মুখার্জী", "hi": "डॉक्टर सौरव मुखर्जी", "en": "Doctor Sourav Mukherjee"}
BHATTACHARYA = {"bn": "ডাক্তার কৌশিক ভট্টাচার্য", "hi": "डॉक्टर कौशिक भट्टाचार्य", "en": "Doctor Kaushik Bhattacharya"}
FRIDAY = {"bn": "শুক্রবার", "hi": "शुक्रवार", "en": "Friday"}
TOMORROW = {"bn": "কাল", "hi": "कल", "en": "tomorrow"}
DAY_AFTER = {"bn": "পরশু", "hi": "परसों", "en": "day after tomorrow"}
# Times are spread so scenarios never book over each other -- except S47, which collides on purpose.
T = {
    "1000": {"bn": "সকাল দশটায়", "hi": "सुबह दस बजे", "en": "at ten in the morning"},
    "1030": {"bn": "সকাল সাড়ে দশটায়", "hi": "सुबह साढ़े दस बजे", "en": "at ten thirty in the morning"},
    "1100": {"bn": "সকাল এগারোটায়", "hi": "सुबह ग्यारह बजे", "en": "at eleven in the morning"},
    "1130": {"bn": "সকাল সাড়ে এগারোটায়", "hi": "सुबह साढ़े ग्यारह बजे", "en": "at eleven thirty in the morning"},
}
# The slot each LANGUAGE books, so the three runs never collide: bn 10:00, hi 10:30, en 11:00. S47 asks for its own
# language's S44 slot again, on purpose.
SLOT = {"bn": T["1000"], "hi": T["1030"], "en": T["1100"]}
EVENING = {
    "bn": {"bn": "সন্ধ্যা ছটায়"},
    "hi": {"hi": "शाम साढ़े छह बजे"},
    "en": {"en": "at seven in the evening"},
}
GHOSH = {"bn": "ডাক্তার প্রবীর ঘোষ", "hi": "डॉक्टर प्रबीर घोष", "en": "Doctor Prabir Ghosh"}
RAHUL = {"bn": "রাহুল বসু", "hi": "राहुल बसु", "en": "Rahul Basu"}
MOTHER = {
    "bn": "আমার মা, সুমিতা দাস, বয়স বাষট্টি",
    "hi": "मेरी माँ, सुमिता दास, उम्र बासठ",
    "en": "my mother, Sumita Das, age sixty two",
}
PRIYA = {"bn": "প্রিয়া সেন", "hi": "प्रिया सेन", "en": "Priya Sen"}

# ---------------------------------------------------------------------------------------------------------------------
# Expectations, per language
# ---------------------------------------------------------------------------------------------------------------------
PRICE = {"bn": r"টাকা|\d", "hi": r"रुपये|रुपए|\d", "en": r"rupee|\d"}
TOTAL = {"bn": r"মোট|সব মিলিয়ে|একসাথে", "hi": r"कुल|मिलाकर", "en": r"total|together|altogether"}
COUNTER = {"bn": r"কাউন্টার", "hi": r"काउंटर", "en": r"counter"}
WEEKDAY = {
    "bn": r"সোমবার|মঙ্গলবার|বুধবার|বৃহস্পতিবার|শুক্রবার|শনিবার|রবিবার",
    "hi": r"सोमवार|मंगलवार|बुधवार|गुरुवार|शुक्रवार|शनिवार|रविवार",
    "en": r"monday|tuesday|wednesday|thursday|friday|saturday|sunday",
}
TIME = {"bn": r"\d{1,2}[:.]\d{2}|টায়", "hi": r"\d{1,2}[:.]\d{2}|बजे", "en": r"\d{1,2}[:.]\d{2}|o'clock|a\.?m|p\.?m"}
BOOKED = {
    "bn": r"কনফার্ম হয়ে গেছে|বুক হয়ে গেছে|কনফার্মেশন নম্বর|KCD",
    "hi": r"बुक हो गया|कन्फ़र्म हो गया|कन्फर्म हो गया|कन्फ़र्मेशन नंबर|KCD",
    "en": r"booked|confirmed|confirmation number|KCD",
}
ASKS = {"bn": r"\?|বলবেন|বলুন", "hi": r"\?|बताइए", "en": r"\?"}
CANCELLED = {"bn": r"বাতিল", "hi": r"रद्द|कैंसिल", "en": r"cancel"}
ALTERNATIVE = {"bn": r"বা |অথবা|নাকি", "hi": r"या |अथवा", "en": r" or "}


def scenarios(lang: str) -> list[dict]:
    """(id, story, opening words, persona, expectations). Expectations: [(pattern, where)] -- where = "any" turn or
    "final" reply. A scenario passes when every expectation holds and every reply is in the caller's language."""
    L = lang
    s: list[dict] = []

    def add(sid, story, opening, expect, who=None, follow=True):
        s.append({"id": sid, "story": story, "opening": opening, "persona": who, "expect": expect, "follow": follow})

    # --- lab tests -------------------------------------------------------------------------------------------------
    add(
        "S17",
        "#17 Caller asks the price of a test",
        {"bn": "সিবিসি টেস্টের দাম কত?", "hi": "सीबीसी टेस्ट का रेट कितना है?", "en": "What is the price of a CBC test?"}[L],
        [(PRICE[L], "any")],
        follow=False,
    )
    add(
        "S17u",
        "#17 unknown test -> not found / near matches",
        {
            "bn": "ভিটামিন সি টেস্টের দাম কত?",
            "hi": "विटामिन सी टेस्ट का रेट कितना है?",
            "en": "What is the price of a vitamin C test?",
        }[L],
        [(ASKS[L], "any")],
        follow=False,
    )
    add(
        "S17m",
        "Multiple lab tests -> each price and the summed total",
        {
            "bn": "সিবিসি আর লিপিড প্রোফাইলের দাম কত?",
            "hi": "सीबीसी और लिपिड प्रोफाइल का रेट कितना है?",
            "en": "What is the price of CBC and lipid profile?",
        }[L],
        [(PRICE[L], "any"), (TOTAL[L], "any")],
        follow=False,
    )
    add(
        "LABBOOK",
        "Lab test booking -> come to the counter",
        {"bn": "আমি একটা সিবিসি টেস্ট বুক করতে চাই", "hi": "मुझे सीबीसी टेस्ट बुक करना है", "en": "I want to book a CBC test"}[L],
        [(COUNTER[L], "any")],
        follow=False,
    )
    add(
        "S18",
        "#18/#19 Report ready / send report -> come to the counter",
        {"bn": "আমার রিপোর্ট কি তৈরি হয়েছে?", "hi": "मेरी रिपोर्ट तैयार हो गई क्या?", "en": "Is my report ready?"}[L],
        [(COUNTER[L], "any")],
        follow=False,
    )
    add(
        "S19",
        "#19 Send my report -> come to the counter",
        {
            "bn": "আমার রিপোর্টটা হোয়াটসঅ্যাপে পাঠিয়ে দিন",
            "hi": "मेरी रिपोर्ट व्हाट्सऐप पर भेज दीजिए",
            "en": "Please send my report on WhatsApp",
        }[L],
        [(COUNTER[L], "any")],
        follow=False,
    )

    # --- doctor scheduling ------------------------------------------------------------------------------------------
    add(
        "S20",
        "#20 Caller asks when a doctor sits",
        {
            "bn": "ডাক্তার দেবাশিস দাস কোন কোন দিন বসেন?",
            "hi": "डॉक्टर देबाशीष दास किस किस दिन बैठते हैं?",
            "en": "Which days does Doctor Debasish Das sit?",
        }[L],
        [(WEEKDAY[L], "any"), (TIME[L], "any")],
        follow=False,
    )
    add(
        "S20u",
        "#20 unknown doctor -> not found / near match",
        {
            "bn": "ডাক্তার পলাশ মিত্র কবে বসেন?",
            "hi": "डॉक्टर पलाश मित्रा कब बैठते हैं?",
            "en": "When does Doctor Palash Mitra sit?",
        }[L],
        [(ASKS[L], "any")],
        follow=False,
    )
    add(
        "S20d",
        "Which departments are there (live call 2026-10-07)",
        {
            "bn": "আপনাদের ওখানে কোন কোন ডিপার্টমেন্ট আছে?",
            "hi": "आपके यहाँ कौन कौन से डिपार्टमेंट हैं?",
            "en": "Which departments do you have?",
        }[L],
        [({"bn": "কার্ডিওলজি|অর্থোপেডিক", "hi": "कार्डियोलॉजी|ऑर्थोपेडिक", "en": "cardiology|orthopaedic"}[L], "any")],
        follow=False,
    )
    add(
        "S46",
        "#46 Caller asks for the earliest available appointment",
        {
            "bn": "ডাক্তার নির্মল রায়ের সবচেয়ে তাড়াতাড়ি কবে অ্যাপয়েন্টমেন্ট পাওয়া যাবে?",
            "hi": "डॉक्टर निर्मल रॉय का सबसे जल्दी अपॉइंटमेंट कब मिलेगा?",
            "en": "When is the earliest appointment with Doctor Nirmal Roy?",
        }[L],
        [(WEEKDAY[L] + "|" + TIME[L], "any")],
        follow=False,
    )

    # --- booking ----------------------------------------------------------------------------------------------------
    slot = SLOT[L][L]
    when = {"bn": f"শুক্রবার {slot}", "hi": f"शुक्रवार {slot}", "en": f"on Friday {slot}"}[L]
    add(
        "S45",
        "#45 Caller names only a doctor (guided through the rest)",
        {
            "bn": "ডাক্তার সৌরভ মুখার্জীর অ্যাপয়েন্টমেন্ট চাই",
            "hi": "डॉक्टर सौरव मुखर्जी का अपॉइंटमेंट चाहिए",
            "en": "I want an appointment with Doctor Sourav Mukherjee",
        }[L],
        [(WEEKDAY[L], "any"), (BOOKED[L], "any")],
        persona(L, MUKHERJEE, FRIDAY, SLOT[L], PRIYA),
    )
    add(
        "S44",
        "#44 Caller gives everything in one sentence",
        {
            "bn": f"আমার নাম রাহুল বসু, {when} ডাক্তার দেবাশিস দাসের সাথে অ্যাপয়েন্টমেন্ট চাই",
            "hi": f"मेरा नाम राहुल बसु है, {when} डॉक्टर देबाशीष दास के साथ अपॉइंटमेंट चाहिए",
            "en": f"My name is Rahul Basu, I want an appointment with Doctor Debasish Das {when}",
        }[L],
        [(BOOKED[L], "any")],
        persona(L, DAS, FRIDAY, SLOT[L], RAHUL, phone=PHONES[L]),
    )
    add(
        "S47",
        "#47 Requested slot is already taken (the slot S44 just booked)",
        {
            "bn": f"{when} ডাক্তার দেবাশিস দাসের অ্যাপয়েন্টমেন্ট চাই",
            "hi": f"{when} डॉक्टर देबाशीष दास का अपॉइंटमेंट चाहिए",
            "en": f"I want an appointment with Doctor Debasish Das {when}",
        }[L],
        [(ALTERNATIVE[L] + "|" + TIME[L], "any")],
        persona(L, DAS, FRIDAY, SLOT[L], PRIYA),
        follow=True,
    )
    add(
        "S48",
        "#48 Caller says tomorrow",
        {
            "bn": f"কাল {slot} ডাক্তার অরিন্দম সেনের অ্যাপয়েন্টমেন্ট চাই",
            "hi": f"कल {slot} डॉक्टर अरिंदम सेन का अपॉइंटमेंट चाहिए",
            "en": f"I want an appointment with Doctor Arindam Sen tomorrow {slot}",
        }[L],
        [({"bn": "বৃহস্পতিবার|৮|8", "hi": "गुरुवार|8", "en": "thursday|8"}[L], "any"), (BOOKED[L], "any")],
        persona(L, SEN, TOMORROW, SLOT[L], PRIYA),
    )
    add(
        "S49",
        "#49 Caller books on behalf of a family member",
        {
            "bn": "আমার মায়ের জন্য ডাক্তার কৌশিক ভট্টাচার্যের অ্যাপয়েন্টমেন্ট চাই",
            "hi": "मेरी माँ के लिए डॉक्टर कौशिक भट्टाचार्य का अपॉइंटमेंट चाहिए",
            "en": "I want to book an appointment with Doctor Kaushik Bhattacharya for my mother",
        }[L],
        [(BOOKED[L], "any")],
        persona(L, BHATTACHARYA, FRIDAY, SLOT[L], MOTHER),
    )
    evening = EVENING[L][L]
    add(
        "S52",
        "#52 Caller cannot give a contact number (day after tomorrow)",
        {
            "bn": f"পরশু {evening} ডাক্তার প্রবীর ঘোষের অ্যাপয়েন্টমেন্ট চাই",
            "hi": f"परसों {evening} डॉक्टर प्रबीर घोष का अपॉइंटमेंट चाहिए",
            "en": f"I want an appointment with Doctor Prabir Ghosh day after tomorrow {evening}",
        }[L],
        [(BOOKED[L], "any")],
        persona(L, GHOSH, DAY_AFTER, EVENING[L], PRIYA, phone=NO_NUMBER[L]),
    )
    add(
        "NAMEONLY",
        "Live call: 'I'll see a doctor' asks WHICH doctor first",
        {"bn": "আমি একজন ডাক্তার দেখাব", "hi": "मुझे एक डॉक्टर को दिखाना है", "en": "I want to see a doctor"}[L],
        [({"bn": "কোন ডাক্তার", "hi": "किस डॉक्टर|कौन से डॉक्टर", "en": "which doctor"}[L], "any")],
        follow=False,
    )

    # --- existing bookings: query, lost reference, reschedule, cancel -----------------------------------------------
    add(
        "S53",
        "#53 Caller asks what they have booked",
        {
            "bn": "আমার কী অ্যাপয়েন্টমেন্ট বুক করা আছে?",
            "hi": "मेरा कौन सा अपॉइंटमेंट बुक है?",
            "en": "What appointment do I have booked?",
        }[L],
        [(ASKS[L], "any")],
        persona(L, DAS, FRIDAY, SLOT[L], RAHUL, phone=PHONES[L]),
    )
    add(
        "S58",
        "#58 Caller has lost their reference number",
        {
            "bn": "আমার কনফার্মেশন নম্বরটা হারিয়ে ফেলেছি, আমার বুকিংটা দেখে বলবেন?",
            "hi": "मेरा कन्फ़र्मेशन नंबर खो गया है, मेरी बुकिंग देखकर बताइए",
            "en": "I lost my confirmation number, can you find my booking?",
        }[L],
        [(ASKS[L], "any")],
        persona(L, DAS, FRIDAY, SLOT[L], RAHUL, phone=PHONES[L]),
    )
    add(
        "S11",
        "#11 Caller moves an existing appointment",
        {
            "bn": "আমার অ্যাপয়েন্টমেন্টটা অন্য দিনে সরাতে চাই",
            "hi": "मुझे अपना अपॉइंटमेंट किसी और दिन करना है",
            "en": "I want to move my appointment to another day",
        }[L],
        [(ASKS[L], "any")],
        persona(L, DAS, {"bn": "সোমবার", "hi": "सोमवार", "en": "Monday"}, SLOT[L], RAHUL, phone=PHONES[L]),
    )
    add(
        "S12",
        "#12 Caller cancels an appointment",
        {
            "bn": "আমার অ্যাপয়েন্টমেন্টটা বাতিল করতে চাই",
            "hi": "मुझे अपना अपॉइंटमेंट रद्द करना है",
            "en": "I want to cancel my appointment",
        }[L],
        [(CANCELLED[L], "any")],
        persona(L, DAS, FRIDAY, SLOT[L], RAHUL, phone=PHONES[L]),
    )
    return s


# ---------------------------------------------------------------------------------------------------------------------
# The caller: answers what the agent actually asked
# ---------------------------------------------------------------------------------------------------------------------
_ASK = {
    "confirm": {
        "bn": r"কনফার্ম করব|ঠিক আছে\?|ঠিক তো|কথা বলছেন\?|বললেন\?|ফিরে যাব|এগোব|করে দেব\?",
        "hi": r"कन्फ़र्म कर|कन्फर्म कर|सही है\?|बात कर रहे हैं\?|पूछ रहे हैं\?|बताया\?|वापस चलें|कर दूँ\?|कर दें\?",
        "en": r"shall i confirm|confirm it|is that right|do you mean|did you say|shall i go back|shall i book|should i",
    },
    "phone": {"bn": r"ফোন নম্বর|নম্বর", "hi": r"फ़ोन नंबर|फोन नंबर|नंबर", "en": r"phone|number"},
    "name": {"bn": r"নাম", "hi": r"नाम", "en": r"name"},
    "doctor": {"bn": r"কোন ডাক্তার", "hi": r"कौन से डॉक्टर|किस डॉक्टर", "en": r"which doctor"},
    "day": {"bn": r"কোন দিন|কবে|তারিখ", "hi": r"कौन सा दिन|किस दिन|तारीख", "en": r"which day|what day|date"},
    "time": {"bn": r"কোন সময়|কটায়|সময়", "hi": r"कौन सा समय|किस समय|समय|बजे", "en": r"what time|which time"},
    "more": {"bn": r"আর কিছু", "hi": r"और कुछ", "en": r"anything else"},
}


def answer_for(reply: str, lang: str, who: dict | None) -> str | None:
    """What the persona says back to `reply`, or None to end the call."""
    r = reply.lower()
    hit = {k: bool(re.search(v[lang], r)) for k, v in _ASK.items()}
    if hit["confirm"]:
        return YES[lang]
    if hit["more"]:
        return NO_MORE[lang]
    if who is None:
        return None
    if not re.search(ASKS[lang], reply):
        return None  # a statement, not a question: nothing was asked (run_scenario decides what a caller says next)
    parts = []
    if hit["doctor"]:
        parts.append(who["doctor"])
    if hit["day"]:
        parts.append(who["day"])
    if hit["time"]:
        parts.append(who["time"])
    if hit["name"] and not parts:
        parts.append(who["name"])
    if hit["phone"] and (not parts or hit["name"]):
        parts.append(who["phone"])
    return ", ".join(parts) or None


# ---------------------------------------------------------------------------------------------------------------------
# The line
# ---------------------------------------------------------------------------------------------------------------------
def synth(text: str, lang: str, out_dir: str, n: int) -> str:
    body = json.dumps({"text": text, "lang": lang}).encode()
    req = urllib.request.Request(TTS_URL, data=body, headers={"Content-Type": "application/json"})
    path = os.path.join(out_dir, f"t{n}.wav")
    with urllib.request.urlopen(req, timeout=60) as resp, open(path, "wb") as f:
        f.write(resp.read())
    return path


async def collect(ws, quiet_s: float, timeout_s: float) -> dict:
    """Everything the agent says until it goes quiet: a filler ("আচ্ছা, বলছি।") is followed by the answer seconds later,
    so stopping at the first text records the filler and loses the answer."""
    import websockets

    turn = {"heard": None, "ai": [], "audio_bytes": 0, "handoff": None, "closed": False}
    deadline = time.monotonic() + timeout_s
    last = time.monotonic()
    while time.monotonic() < deadline:
        wait = min(deadline, last + quiet_s) - time.monotonic()
        if wait <= 0 and (turn["ai"] or turn["audio_bytes"]):
            break
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=max(0.05, wait))
        except TimeoutError:
            if turn["ai"] or turn["audio_bytes"]:
                break
            continue
        except websockets.exceptions.ConnectionClosed:
            turn["closed"] = True
            break
        last = time.monotonic()
        if isinstance(msg, bytes):
            turn["audio_bytes"] += len(msg)
            continue
        data = json.loads(msg)
        if data.get("type") == "handoff_human":
            turn["handoff"] = data.get("reason")
        elif data.get("sender") == "User":
            turn["heard"] = data["text"]
        elif data.get("sender") == "AI":
            turn["ai"].append(data["text"])
    return turn


def script_lang(text: str) -> str | None:
    """Which language a reply is written in, by script: the voice can only say its own."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    bn = sum("ঀ" <= c <= "৿" for c in letters) / len(letters)
    hi = sum("ऀ" <= c <= "ॿ" for c in letters) / len(letters)
    en = sum(c.isascii() for c in letters) / len(letters)
    best = max((bn, "bn"), (hi, "hi"), (en, "en"))
    return best[1] if best[0] >= 0.5 else None


async def run_scenario(sc: dict, lang: str, port: int) -> dict:
    import websockets

    out_dir = tempfile.mkdtemp(prefix=f"live_{sc['id']}_{lang}_")
    turns = []
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws/audio", max_size=None) as ws:
        await ws.send(json.dumps({"type": "hello", "sampleRate": 16000, "format": "pcm_s16le", "channels": 1}))
        greeting = await collect(ws, 2.5, 30)
        await ws.send(json.dumps({"type": "playback_done"}))
        said = sc["opening"]
        asked_to_book = False
        for n in range(MAX_TURNS):
            wav = synth(said, lang, out_dir, n)
            await asyncio.sleep(1.2)  # the server resyncs after playback_done; a caller cannot talk over it
            await stream(ws, load_16k_mono(wav), 1.0)  # it sends the tail silence too
            got = await collect(ws, QUIET_S, 45)
            await ws.send(json.dumps({"type": "playback_done"})) if not got["closed"] else None
            reply = " ".join(got["ai"])
            turns.append(
                {
                    "said": said,
                    "heard": got["heard"],
                    "reply": reply,
                    "reply_lang": script_lang(reply),
                    "handoff": got["handoff"],
                    "closed": got["closed"],
                }
            )
            if got["closed"] or got["handoff"] or not sc["follow"]:
                break
            nxt = answer_for(reply, lang, sc["persona"])
            booked_yet = any(re.search(BOOKED[lang], t["reply"]) for t in turns)
            if not nxt and sc["persona"] and not booked_yet and not asked_to_book:
                # The agent made a statement ("Dr. Ghosh sits 18:00-20:00 that day") but the caller came to BOOK: a
                # real caller says so. Once -- a second statement ends the call, so a loop cannot hide here.
                nxt, asked_to_book = BOOK_IT[lang], True
            if not nxt or nxt == said and n > 0:
                break
            said = nxt
    replies = [t["reply"] for t in turns if t["reply"]]
    checks = []
    for pattern, where in sc["expect"]:
        pool = replies if where == "any" else replies[-1:]
        checks.append({"expect": pattern, "where": where, "ok": any(re.search(pattern, r, re.I) for r in pool)})
    wrong_lang = [t["reply"] for t in turns if t["reply"] and t["reply_lang"] not in (lang, None)]
    return {
        "id": sc["id"],
        "story": sc["story"],
        "lang": lang,
        "greeting": " ".join(greeting["ai"]),
        "turns": turns,
        "checks": checks,
        "wrong_language_replies": wrong_lang,
        "passed": all(c["ok"] for c in checks) and not wrong_lang,
    }


def reset_test_bookings() -> int:
    """Remove ONLY what earlier runs of this script booked: appointments under its test phone numbers or test patient
    names, and every slot lock (holds expire on their own; a run must not inherit a dead one). Nothing else."""
    import sqlite3

    db = sqlite3.connect(os.environ.get("CLINIC_DB_PATH", "/workspace/clinic.db"))
    names = [n for d in (RAHUL, PRIYA) for n in d.values()] + ["Sumita Das", "সুমিতা দাস", "सुमिता दास"]
    phones = ",".join("?" * len(TEST_PHONES))
    people = ",".join("?" * len(names))
    sql = f"DELETE FROM appointments WHERE phone IN ({phones}) OR patient_name IN ({people})"
    n = db.execute(sql, (*TEST_PHONES, *names)).rowcount
    db.execute("DELETE FROM slot_locks")
    db.commit()
    db.close()
    return n


async def main_async(args) -> list[dict]:
    if args.reset:
        print(f"reset: removed {reset_test_bookings()} test booking(s)", flush=True)
    results = []
    for lang in args.langs.split(","):
        for sc in scenarios(lang):
            if args.only and sc["id"] not in args.only.split(","):
                continue
            t0 = time.monotonic()
            try:
                res = await run_scenario(sc, lang, args.port)
            except Exception as e:  # noqa: BLE001 - one broken scenario must not hide the others
                res = {
                    "id": sc["id"],
                    "story": sc["story"],
                    "lang": lang,
                    "turns": [],
                    "checks": [],
                    "wrong_language_replies": [],
                    "passed": False,
                    "error": f"{type(e).__name__}: {e}",
                }
            res["seconds"] = round(time.monotonic() - t0, 1)
            results.append(res)
            mark = "PASS" if res["passed"] else "FAIL"
            print(f"[{mark}] {lang} {res['id']:9} {res['story'][:60]}  ({res['seconds']}s)", flush=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=1)
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=10100)
    ap.add_argument("--langs", default="bn,hi,en")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="/workspace/logs/live_stories.json")
    ap.add_argument("--reset", action="store_true", help="first remove the bookings earlier runs of this script made")
    args = ap.parse_args()
    results = asyncio.run(main_async(args))
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} passed -> {args.out}")


if __name__ == "__main__":
    main()
