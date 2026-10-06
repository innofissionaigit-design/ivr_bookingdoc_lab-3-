#!/usr/bin/env python3
"""Score the lookup-vs-book decision against a labelled set.

story: "Caller asks what they have booked"

TWO THINGS DECIDE THIS INTENT, and only one of them can be measured off the pod:

  * agent/lookup_request.py -- the deterministic net. Measured here, fully, right now.
  * agent/llm.py -- the model, which handles everything the net abstains on. Needs Ollama and
    Qwen, so `--model` is OFF by default and the report says so rather than implying otherwise.

THE FOUR NUMBERS THAT MATTER, in the order they matter:

  existing -> book    CRITICAL. The caller wanted to be reminded of a booking and is given a
                      SECOND one instead. This is the defect the reschedule story was written to
                      fix, reached by another route, and the number that must be zero.
  existing -> existing
  book -> book
  book -> existing    A caller who wants to book is read their old booking. Wastes a turn,
                      corrects itself, nowhere near as bad as the first.

ABSTAIN is not an error for the net: it means "the model decides", which is the designed
fallback. It is reported separately so the net's COVERAGE is visible and never confused with
its accuracy.

    python tools/lookup_intent_eval.py              # the net alone, no services needed
    python tools/lookup_intent_eval.py --model      # also ask the real Qwen (needs the pod)
    python tools/lookup_intent_eval.py --show-misses
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.lookup_request import asks_what_is_booked, wants_new_booking  # noqa: E402

# ---------------------------------------------------------------------------
# THE LABELLED SET.
#
# REASONED, NOT HARVESTED. These are written, not transcribed from real calls -- no call
# recordings exist for this clinic yet. The Bengali is modelled on the wording this project's own
# templates use; the Hindi and the romanised forms were written by a NON-NATIVE reviewer, the same
# caveat agent/fast_path_cues.py and agent/lookup_request.py both carry. A native speaker should
# review these lists before any number below is quoted as an accuracy figure, and real ASR output
# should replace them when there is some: a recogniser's spelling of "অ্যাপয়েন্টমেন্ট" is the
# thing that actually has to match.
# ---------------------------------------------------------------------------

EXISTING: dict[str, list[str]] = {
    "bn": [
        "আমার অ্যাপয়েন্টমেন্ট কী আছে?",
        "আমার কী বুক করা আছে?",
        "আমার অ্যাপয়েন্টমেন্টটা কবে?",
        "আমার অ্যাপয়েন্টমেন্ট কোন দিন?",
        "আমার অ্যাপয়েন্টমেন্ট কখন?",
        "আমার একটা অ্যাপয়েন্টমেন্ট আছে?",
        "আমার নামে কোনো অ্যাপয়েন্টমেন্ট আছে?",
        "আমি কবে অ্যাপয়েন্টমেন্ট করেছিলাম?",
        "আমি অ্যাপয়েন্টমেন্ট করেছিলাম, কবে সেটা?",
        "আমার অ্যাপয়েন্টমেন্ট কখন ছিল?",
        "আমার বুকিংটা কবে?",
        "আমার বুকিং আছে কি?",
        "আমার টেস্টের বুকিং কবে?",
        "অ্যাপয়েন্টমেন্টটা কবে আছে আমার?",
        "আমার অ্যাপয়েন্টমেন্ট কটায়?",
        "আমার সিরিয়াল কবে?",
        "আমার অ্যাপয়েন্টমেন্টের দিনটা ভুলে গেছি, কবে আছে?",
        "আমার অ্যাপয়েন্টমেন্ট বুক করা আছে তো?",
        "আমার অ্যাপয়েন্টমেন্ট কনফার্ম আছে কি?",
        "আমার অ্যাপয়েন্টমেন্ট কোন দিন দেওয়া আছে?",
        "আমার কি কোনো বুকিং আছে?",
        "আমার অ্যাপয়েন্টমেন্ট হয়েছিল কবে?",
        "আমার বুকিং কনফার্ম হয়েছিল, কবে?",
        "আমার অ্যাপয়েন্টমেন্ট কবে দেওয়া আছে?",
        "আমাদের অ্যাপয়েন্টমেন্ট কবে?",
        "ডাক্তার সেনের কাছে আমার অ্যাপয়েন্টমেন্ট কবে?",
        "রাহুল দাসের নামে অ্যাপয়েন্টমেন্ট কবে আছে?",
        "আমার মায়ের অ্যাপয়েন্টমেন্ট কবে?",
        "আগামী সপ্তাহে আমার অ্যাপয়েন্টমেন্ট আছে?",
        "কাল আমার অ্যাপয়েন্টমেন্ট আছে কি?",
        "এই মাসে আমার কোনো অ্যাপয়েন্টমেন্ট আছে?",
        "মেসেজটা হারিয়ে ফেলেছি, আমার অ্যাপয়েন্টমেন্ট কবে?",
        "কনফার্মেশন মেসেজ পাইনি, আমার বুকিং কবে?",
        "একটু দেখে বলবেন আমার অ্যাপয়েন্টমেন্ট কবে?",
        "আমার অ্যাপয়েন্টমেন্টের সময়টা একবার বলবেন?",
        "আমার বুকিংটা ঠিক আছে তো?",
        "আমার অ্যাপয়েন্টমেন্ট এখনও আছে তো?",
        "আমার রক্ত পরীক্ষার বুকিং কবে আছে?",
        "আমার টেস্ট কবে করা আছে?",
        "আমার অ্যাপয়েন্টমেন্ট কোন ডাক্তারের কাছে আছে?",
        "আমি কোন ডাক্তারের কাছে বুক করেছিলাম?",
        "আমার অ্যাপয়েন্টমেন্ট নম্বরটা কী?",
        "আমার কনফার্মেশন নম্বরটা কী ছিল?",
        "আমার দুটো অ্যাপয়েন্টমেন্ট আছে কি?",
        "আমার অ্যাপয়েন্টমেন্ট বাতিল হয়ে যায়নি তো?",
        "আমার অ্যাপয়েন্টমেন্ট কবে, একটু দেখুন",
        "দেখুন তো আমার নামে কী বুক করা আছে",
        "আমার সিরিয়াল নম্বর কত ছিল?",
        "আমার অ্যাপয়েন্টমেন্ট সকালে না বিকেলে ছিল?",
        "আমার অ্যাপয়েন্টমেন্টটা কোন সময় দেওয়া আছে?",
    ],
    "hi": [
        "मेरी अपॉइंटमेंट क्या है?",
        "मेरी अपॉइंटमेंट कब है?",
        "मेरी अपॉइंटमेंट कब थी?",
        "मेरी अपॉइंटमेंट किस दिन है?",
        "मेरी अपॉइंटमेंट कितने बजे है?",
        "मेरी बुकिंग कब है?",
        "मेरी कोई बुकिंग है क्या?",
        "मेरी अपॉइंटमेंट बुक है क्या?",
        "मेरा अपॉइंटमेंट कब का है?",
        "मैंने अपॉइंटमेंट करवाया था, कब है?",
        "मैंने बुकिंग कराया था, किस दिन?",
        "मेरी अपॉइंटमेंट का दिन भूल गया, कब है?",
        "मेरे नाम पर कोई अपॉइंटमेंट है?",
        "मेरी टेस्ट की बुकिंग कब है?",
        "मेरी अपॉइंटमेंट कन्फ़र्म है?",
        "अपॉइंटमेंट मेरी कब है?",
        "मेरा सीरियल कब है?",
        "मेरी अपॉइंटमेंट का समय क्या था?",
        "हमारी अपॉइंटमेंट कब है?",
        "मेरी अपॉइंटमेंट कब बुक है?",
        "डॉक्टर सेन के साथ मेरी अपॉइंटमेंट कब है?",
        "राहुल दास के नाम पर अपॉइंटमेंट कब है?",
        "मेरी माँ की अपॉइंटमेंट कब है?",
        "अगले हफ़्ते मेरी अपॉइंटमेंट है क्या?",
        "कल मेरी अपॉइंटमेंट है क्या?",
        "इस महीने मेरी कोई अपॉइंटमेंट है?",
        "मैसेज खो गया है, मेरी अपॉइंटमेंट कब है?",
        "कन्फ़र्मेशन मैसेज नहीं मिला, मेरी बुकिंग कब है?",
        "ज़रा देखकर बताइए मेरी अपॉइंटमेंट कब है",
        "मेरी अपॉइंटमेंट का समय एक बार बता दीजिए",
        "मेरी बुकिंग ठीक है ना?",
        "मेरी अपॉइंटमेंट अभी भी है ना?",
        "मेरे खून की जाँच की बुकिंग कब है?",
        "मेरा टेस्ट कब का बुक है?",
        "मेरी अपॉइंटमेंट किस डॉक्टर के साथ है?",
        "मैंने किस डॉक्टर के साथ बुक किया था?",
        "मेरा अपॉइंटमेंट नंबर क्या है?",
        "मेरा कन्फ़र्मेशन नंबर क्या था?",
        "मेरी दो अपॉइंटमेंट हैं क्या?",
        "मेरी अपॉइंटमेंट रद्द नहीं हुई ना?",
        "ज़रा देखिए मेरी अपॉइंटमेंट कब है",
        "देखिए मेरे नाम पर क्या बुक है",
        "मेरा सीरियल नंबर कितना था?",
        "मेरी अपॉइंटमेंट सुबह थी या शाम?",
        "मेरी अपॉइंटमेंट किस समय दी गई है?",
        "मेरी अपॉइंटमेंट का दिन कौन सा है?",
        "क्या मेरी कोई अपॉइंटमेंट बाकी है?",
        "मेरी आने वाली अपॉइंटमेंट कब है?",
        "मेरी अगली बुकिंग किस दिन है?",
        "मेरी अपॉइंटमेंट हो चुकी थी या अभी बाकी है?",
    ],
    "en": [
        "what have I got booked?",
        "do I have anything booked?",
        "when is my appointment?",
        "what's my appointment?",
        "did I book an appointment?",
        "do I have an appointment?",
        "what is my booking?",
        "when's my booking?",
        "my appointment is when?",
        "have I got an appointment next week?",
        "I forgot when my appointment is",
        "could you tell me my appointment date?",
        "is my appointment confirmed?",
        "what time is my appointment?",
        "when is my test booking?",
        "do I have a slot booked?",
        "what day is my appointment on?",
        "remind me when my appointment is",
        "my booking -- when is it?",
        "what appointment do I have?",
        "when is my appointment with Doctor Sen?",
        "is there an appointment booked for Rahul Das?",
        "when is my mother's appointment?",
        "have I got an appointment this week?",
        "do I have an appointment tomorrow?",
        "do I have anything booked this month?",
        "I lost the message, when is my appointment?",
        "I never got the confirmation, when is my booking?",
        "could you check when my appointment is?",
        "could you read my appointment time back to me?",
        "is my booking still alright?",
        "is my appointment still there?",
        "when is my blood test booked for?",
        "when is my test booking?",
        "which doctor is my appointment with?",
        "who did I book with?",
        "what's my appointment number?",
        "what was my confirmation number?",
        "do I have two appointments?",
        "my appointment hasn't been cancelled, has it?",
        "please check what I have booked",
        "have a look at what's booked in my name",
        "what was my serial number?",
        "was my appointment in the morning or the evening?",
        "what time has my appointment been given?",
        "what day is my booking on?",
        "is there an appointment left for me?",
        "when is my upcoming appointment?",
        "when is my next booking?",
        "has my appointment already happened or is it still to come?",
    ],
    "mixed": [
        "amar appointment ta kobe?",
        "amar appointment ache?",
        "amar booking kobe?",
        "amar appointment kokhon?",
        "amar appointment ta kon din?",
        "meri appointment kab hai?",
        "meri booking kab hai?",
        "mera appointment kab ka hai?",
        "amar appointment confirm ache?",
        "amar test er booking kobe?",
        "amar appointment korechilam, kobe?",
        "my appointment ta kobe?",
        "amar appointment er date ta ki?",
        "meri appointment ka time kab hai?",
        "amar serial kobe ache?",
        "amar appointment ta doctor Sen er kache kobe?",
        "amar maa er appointment kobe?",
        "next week amar appointment ache?",
        "kal amar appointment ache ki?",
        "message ta hariye gache, amar appointment kobe?",
        "ektu dekhe bolben amar appointment kobe?",
        "amar booking thik ache to?",
        "amar blood test er booking kobe?",
        "amar appointment kon doctor er kache?",
        "amar confirmation number ta ki chilo?",
        "meri appointment doctor Sen ke saath kab hai?",
        "agle hafte meri appointment hai kya?",
        "meri booking thik hai na?",
        "mera serial number kitna tha?",
        "meri appointment kis doctor ke saath hai?",
    ],
}

NEW_BOOKING: dict[str, list[str]] = {
    "bn": [
        "আমার একটা অ্যাপয়েন্টমেন্ট চাই",
        "আমি একটা অ্যাপয়েন্টমেন্ট করতে চাই",
        "অ্যাপয়েন্টমেন্ট চাই",
        "আমার জন্য একটা অ্যাপয়েন্টমেন্ট চাই",
        "একটা অ্যাপয়েন্টমেন্ট করে দিন",
        "ডাক্তার সেনের কাছে অ্যাপয়েন্টমেন্ট চাই",
        "আমার মায়ের জন্য অ্যাপয়েন্টমেন্ট চাই",
        "কাল একটা অ্যাপয়েন্টমেন্ট করতে চাই",
        "একটা বুকিং করে দিন",
        "অ্যাপয়েন্টমেন্ট নিতে চাই",
    ],
    "hi": [
        "मुझे अपॉइंटमेंट चाहिए",
        "मुझे एक अपॉइंटमेंट चाहिए",
        "अपॉइंटमेंट चाहिए",
        "मुझे अपॉइंटमेंट बुक करना है",
        "अपॉइंटमेंट करवाना है",
        "डॉक्टर सेन के साथ अपॉइंटमेंट चाहिए",
        "कल का अपॉइंटमेंट चाहिए",
        "एक बुकिंग करा दीजिए",
        "मुझे अपॉइंटमेंट लेना है",
        "मेरी माँ के लिए अपॉइंटमेंट चाहिए",
    ],
    "en": [
        "I want an appointment",
        "I need an appointment",
        "can I book an appointment?",
        "book me an appointment",
        "I'd like an appointment",
        "I would like to book an appointment",
        "please book an appointment for me",
        "can I get an appointment tomorrow?",
        "make me an appointment with Doctor Sen",
        "I want to book a slot",
    ],
    "mixed": [
        "amar ekta appointment chai",
        "appointment chai",
        "mujhe appointment chahiye",
        "ekta appointment korte chai",
        "amar ekta booking lagbe",
        "appointment korte chai kal",
        "mujhe ek appointment chahiye doctor Sen ke saath",
        "amar jonno appointment dorkar",
    ],
}


def net_verdict(text: str) -> str:
    """"existing" | "book" | "abstain" -- what the deterministic net alone decides."""
    if asks_what_is_booked(text):
        return "existing"
    if wants_new_booking(text):
        return "book"
    return "abstain"


def _score(cases: dict[str, list[str]], truth: str, verdict) -> dict:
    out = {}
    for bucket, items in cases.items():
        counts = {"existing": 0, "book": 0, "abstain": 0}
        misses = []
        for text in items:
            got = verdict(text)
            counts[got] += 1
            if got != truth and got != "abstain":
                misses.append((text, got))
        out[bucket] = {"n": len(items), **counts, "misses": misses}
    return out


def _table(title: str, scored: dict, truth: str, other: str) -> list[str]:
    lines = [title, "-" * len(title),
             "%-8s %5s %9s %9s %9s" % ("bucket", "n", "correct", other.upper(), "abstain")]
    tot = {"n": 0, "correct": 0, "wrong": 0, "abstain": 0}
    for bucket, r in scored.items():
        lines.append("%-8s %5d %9d %9d %9d" % (bucket, r["n"], r[truth], r[other], r["abstain"]))
        tot["n"] += r["n"]
        tot["correct"] += r[truth]
        tot["wrong"] += r[other]
        tot["abstain"] += r["abstain"]
    lines.append("%-8s %5d %9d %9d %9d" % ("TOTAL", tot["n"], tot["correct"], tot["wrong"], tot["abstain"]))
    return lines


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", action="store_true",
                    help="also classify with the real model (needs Ollama and Qwen on the pod)")
    ap.add_argument("--show-misses", action="store_true")
    args = ap.parse_args(argv[1:])

    verdict = net_verdict
    label = "DETERMINISTIC NET (agent/lookup_request.py)"
    if args.model:
        from agent.llm import extract_intent

        def verdict(text: str) -> str:  # noqa: F811
            if asks_what_is_booked(text):
                return "existing"  # the net short-circuits the model in production too
            data, _ = extract_intent(text, 2, "bn", 20.0)
            intent = (data or {}).get("intent")
            if intent == "lookup_booking":
                return "existing"
            if intent == "book_appointment":
                return "book"
            return "abstain"

        label = "NET + REAL MODEL"

    existing = _score(EXISTING, "existing", verdict)
    booking = _score(NEW_BOOKING, "book", verdict)

    print(label)
    print("=" * len(label))
    print()
    print("\n".join(_table("asking about an EXISTING booking", existing, "existing", "book")))
    print()
    print("\n".join(_table("asking to BOOK a new one", booking, "book", "existing")))
    print()

    critical = sum(r["book"] for r in existing.values())
    reverse = sum(r["existing"] for r in booking.values())
    print("CRITICAL  existing -> book : %d   (a caller asking what they have is given a second booking)" % critical)
    print("          book -> existing : %d   (a caller wanting to book hears their old one; costs a turn)" % reverse)
    if not args.model:
        n_abstain = sum(r["abstain"] for r in existing.values()) + sum(r["abstain"] for r in booking.values())
        print()
        print("%d of %d utterances ABSTAINED: the net took no view and the MODEL decides them."
              % (n_abstain, sum(r["n"] for r in existing.values()) + sum(r["n"] for r in booking.values())))
        print("Those are NOT measured here. Re-run with --model on the pod to measure them.")

    if args.show_misses:
        print()
        for name, scored in (("EXISTING misread", existing), ("BOOK misread", booking)):
            for bucket, r in scored.items():
                for text, got in r["misses"]:
                    print("  %-16s %-7s -> %-8s %s" % (name, bucket, got, text))

    return 1 if critical else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
