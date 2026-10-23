"""agent/location_question.py: is the caller asking WHERE the clinic is, or HOW TO GET THERE?

Measured on the pod on 2026-10-13 (the brief of 90 indirect address questions, real fast path + real Qwen): 55 got the
address and 35 did not -- 9 because the keyword matcher took them to the WRONG topic (a doctor's schedule for "আপনারা
কোথায় বসেন", the contact number for "আপনাদের সেন্টারটা কি মেইন রোডের ওপর?"), 26 because the model called the caller
unrelated or composed a sentence of its own.

This file holds the 90 (every one must be the address) and, as important, what must NOT be: a doctor's schedule, a
visit tomorrow, the hours, a price, department routing for a symptom.

    python -m pytest tests/test_location_question.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent.location_question import asks_where_or_how_to_reach as asks  # noqa: E402

BRIEF = [
    # 1. indirect: where to go
    "আমি আপনাদের কাছে আসতে চাই।", "আপনাদের কাছে আসতে হলে কোথায় আসব?", "কোথায় এলে আপনাদের পাব?",
    "আপনাদের সঙ্গে দেখা করতে কোথায় যেতে হবে?", "আপনাদের কাছে যাওয়ার জন্য কী করতে হবে?", "আপনাদের এখানে কীভাবে আসব?",
    "আপনাদের কাছে পৌঁছব কী করে?", "আপনাদের কাছে যেতে চাই, কোথায় যাব?", "আপনাদের সেন্টারে আসতে চাইছিলাম।",
    "আপনাদের ওখানে যাওয়ার কথা ভাবছি।", "আপনাদের এখানে যেতে হলে কোন দিকে যেতে হবে?",
    "আপনাদের কাছে আসার রাস্তাটা বলবেন?", "আমি আপনাদের ওখানে যাব, কোথায় আসতে হবে?",
    "আপনাদের সঙ্গে দেখা করতে হলে কোথায় আসব বলুন তো?", "আপনাদের কাছে যাওয়ার জন্য কোন জায়গায় যেতে হবে?",
    # 2. the clinic's location, indirectly
    "আপনাদের সেন্টারটা কোন দিকে?", "আপনারা কোথায় বসেন?", "আপনাদের বসার জায়গাটা কোথায়?", "আপনাদের অফিসটা কোন দিকে?",
    "আপনাদের প্রতিষ্ঠানটা কোথায়?", "আপনাদের সেটআপটা কোথায় রয়েছে?", "আপনাদের শাখাটা কোথায় পড়ছে?",
    "আপনাদের ক্লিনিকটা কোন জায়গায় পড়ে?", "আপনারা কোন জায়গা থেকে পরিষেবা দেন?",
    "আপনাদের পরিষেবা কোথা থেকে পাওয়া যাবে?", "আপনাদের সেন্টারটা কি কলকাতার মধ্যে?", "আপনারা কি কলকাতাতেই আছেন?",
    "আপনাদের কোথায় পাওয়া যাবে বলুন তো?", "আপনারা কোন এলাকায় বসেন?", "আপনাদের জায়গাটা কোথায়?",
    # 3. how to reach
    "আপনাদের ওখানে যাওয়ার উপায়টা কী?", "আপনাদের কাছে যাওয়ার রাস্তাটা কী?", "কোন রাস্তা দিয়ে গেলে আপনাদের কাছে পৌঁছব?",
    "আপনাদের কাছে যেতে হলে কোন দিকে যাব?", "আপনাদের ওখানে যাওয়ার জন্য কীভাবে এগোব?",
    "আপনাদের সেন্টারে পৌঁছনোর উপায় কী?", "আপনাদের কাছে যাওয়ার পথটা একটু বুঝিয়ে দেবেন?",
    "আপনাদের এখানে আসার জন্য কোন রাস্তা ধরব?", "আপনাদের কাছে যেতে হলে কোথায় নামতে হবে?",
    "আপনাদের ওখানে যাওয়ার জন্য কোন স্টপে নামব?", "কোন জায়গা থেকে আপনাদের কাছে যাওয়া সহজ হবে?",
    "আপনাদের কাছে যাওয়ার জন্য কোন গাড়ি পাব?", "আপনাদের ওখানে যাওয়ার রাস্তা কি সহজ?",
    "আপনাদের কাছে যাওয়ার জন্য কাউকে জিজ্ঞেস করলে কী বলব?", "আপনাদের সেন্টারে পৌঁছতে কোন দিকটা ধরতে হবে?",
    # 4. nearby places, landmarks
    "আপনাদের সেন্টারটা কীসের কাছে?", "আপনাদের ওখানের আশেপাশে কী আছে?", "আপনাদের ক্লিনিকের কাছাকাছি কী কী রয়েছে?",
    "আপনাদের ওখানে যাওয়ার জন্য পরিচিত কোনো জায়গা আছে?", "আপনাদের সেন্টারটা কোন বড় জায়গার পাশে?",
    "আপনাদের ওখানে কি কোনো বাসস্ট্যান্ড আছে?", "আপনাদের ক্লিনিকের কাছে কি কোনো মেট্রো স্টেশন আছে?",
    "আপনাদের ওখানে যেতে হলে কোন পরিচিত জায়গায় যাব?", "আপনাদের সেন্টারটা কি কোনো হাসপাতালের কাছে?",
    "আপনাদের ওখানটা চিনব কী করে?", "আপনাদের জায়গাটা চেনার জন্য কোনো চিহ্ন আছে?",
    "আপনাদের কাছে নামার মতো কোনো পরিচিত জায়গা আছে?", "আপনাদের ক্লিনিকের কাছের মোড়টার নাম কী?",
    "আপনাদের ওখানে কোন ল্যান্ডমার্ক ধরে যেতে পারব?", "আপনাদের সেন্টারটা কি মেইন রোডের ওপর?",
    # 5. natural phone-call phrasing
    "আচ্ছা, আপনাদের ওখানে যেতে হলে কী করতে হবে?", "আমি একবার আপনাদের সেন্টারে আসতে চাইছিলাম।",
    "আপনাদের কাছে সরাসরি চলে যেতে পারব তো?", "আপনাদের ওখানে আসার জন্য কোথায় যেতে হবে একটু বলুন।",
    "আমি আগে কখনও আপনাদের ওখানে যাইনি, কীভাবে যাব?", "আপনাদের সেন্টারটা আমার চেনা নেই, একটু বুঝিয়ে বলবেন?",
    "আপনাদের কাছে আসতে চাই, কিন্তু জায়গাটা জানি না।",
    "আপনাদের ওখানে যাওয়ার জন্য কাউকে সঙ্গে নিতে হবে, নাকি সহজেই খুঁজে পাব?",
    "আমি এখন বাইরে আছি, আপনাদের কাছে যেতে হলে কোন দিকে যাব?", "আপনাদের ওখানে পৌঁছনোর জন্য কী কী চিনে রাখতে হবে?",
    "আপনাদের সেন্টারে প্রথমবার যাব, কীভাবে খুঁজে পাব?", "আপনাদের ওখানে যাওয়ার আগে জায়গাটা একটু বুঝে নিতে চাই।",
    "আপনাদের ওখানে গেলে কাউকে জিজ্ঞেস করতে হবে?", "আপনাদের সেন্টারটা কি সহজে খুঁজে পাওয়া যায়?",
    "আপনাদের কাছে যাওয়ার ব্যাপারটা একটু বুঝিয়ে বলবেন?",
    # 6. very indirect
    "আপনাদের ওখানে আসার কথা ছিল।", "আপনাদের এখানে আসতে চাইলে কী করতে হবে?", "আপনাদের সঙ্গে যোগাযোগ করে কোথায় আসব?",
    "আপনাদের ওখানে যাওয়ার জন্য একটু সাহায্য করবেন?", "আপনাদের সেন্টারে আসার ব্যাপারে জানতে চাই।",
    "আপনাদের এখানে কীভাবে যাওয়া যায়?", "আপনাদের ওখানে যাওয়ার জায়গাটা একটু বলুন।", "আপনাদের কাছে আসার ব্যবস্থা কী?",
    "আপনাদের সঙ্গে দেখা করার জায়গাটা কোথায়?", "আপনাদের এখানে গেলে কোথায় যেতে হবে?",
    "আপনাদের ওখানে আসার জন্য কাউকে ফোন করতে হবে?", "আপনাদের সেন্টারে পৌঁছনোর ব্যাপারে একটু বলবেন?",
    "আপনাদের কাছে যাওয়ার জন্য কী কী জানা দরকার?", "আপনাদের ওখানে যাওয়ার আগে জায়গাটা জেনে নিতে চাই।",
    "আপনাদের এখানে আসার জন্য একটু গাইড করবেন?",
]

# the phrasings the FAQ keywords already caught: the net agrees with them
PLAIN = [
    "আপনাদের ঠিকানা কী", "আপনাদের ক্লিনিক কোথায়", "আপনাদের ওখানে যাব কীভাবে?", "আপনাদের ওখানে যাব কিভাবে",
    "আপনাদের ওখানেই যাব কি ভাবে", "ক্লিনিকের কাছাকাছি কোন ল্যান্ডমার্ক আছে", "আপনাদের ক্লিনিকে কিভাবে যেতে হবে",
    "আপনাদের ক্লিনিক কোন রাস্তায়", "ক্লিনিকে যাওয়ার রাস্তা কোনটা",
]

# what is NOT a question about where the clinic is, each with the path that already takes it
NOT_THE_ADDRESS = [
    # a doctor's schedule
    "ডাক্তার সেন কোথায় বসেন", "আপনাদের ডাক্তার কোথায় বসেন", "ডাঃ ব্যানার্জী কখন বসেন", "আপনাদের ওখানে কোন ডাক্তার আছেন",
    "আপনাদের ওখানে গাইনির ডাক্তার আছেন কি", "আপনাদের ডাক্তারদের নাম বলুন",
    # a visit at a time or on a day
    "আমি কাল আপনাদের ওখানে আসব", "পরশু সকালে আপনাদের ওখানে যাব", "আজ বিকেলে আপনাদের কাছে আসতে পারব", "রবিবার আসব",
    "আমি সকাল দশটায় আপনাদের ওখানে যেতে চাই", "আপনাদের ওখানে কাল যেতে চাই",
    # the hours
    "আপনাদের ক্লিনিক কখন খোলে", "আপনাদের ক্লিনিক কটায় খোলে", "আপনারা কি রবিবার খোলা থাকেন",
    "আপনাদের সেন্টার কখন বন্ধ হয়", "আপনাদের সময়টা কী",
    # a booking
    "আপনাদের ওখানে অ্যাপয়েন্টমেন্ট নিতে চাই", "আপনাদের ওখানে সিরিয়াল দিতে চাই", "আমার অ্যাপয়েন্টমেন্ট বাতিল করতে চাই",
    "আমি আপনাদের ওখানে ডাক্তার দেখাতে চাই", "আমাকে আপনাদের ওখানে দেখাতে যেতে হবে",
    # a test, a price, a report
    "আপনাদের ওখানে সিবিসি টেস্ট কত", "আপনাদের ওখানে ব্লাড টেস্ট কোথায় হয়", "আপনাদের রিপোর্ট কোথায় পাব",
    "রিপোর্ট নিতে আপনাদের কোথায় যেতে হবে", "আপনাদের ওখানে স্যাম্পল দিতে কোথায় যাব", "আপনাদের ফি কত",
    # the other FAQs
    "আপনাদের পার্কিং কোথায়", "আপনাদের ওখানে গাড়ি রাখব কোথায়", "আপনাদের ওখানে ইনসুরেন্স চলে", "আপনাদের ওখানে কার্ডে পেমেন্ট হয়",
    "আপনাদের ফোন নম্বর কত", "আপনাদের হোয়াটসঅ্যাপ নম্বর কী", "আপনারা কি বাড়ি থেকে স্যাম্পল নেন",
    # a symptom (department routing: "where should I go with this?")
    "আমার পেটে ব্যথা আপনাদের ওখানে কোথায় যাব", "আমার বাচ্চার জ্বর আপনাদের ওখানে কোন ডাক্তারের কাছে যাব",
    "বুকে ব্যথা নিয়ে আপনাদের ওখানে কোথায় যেতে হবে", "আমার ত্বকের সমস্যা কোথায় দেখাব",
    # who, what, how many: not where
    "আপনারা কারা", "আপনাদের কাজ কী", "আপনারা কী কী পরিষেবা দেন", "আপনাদের ওখানে কোন কোন ডিপার্টমেন্ট রয়েছে",
    "আপনাদের হাসপাতালের মালিক কে", "সব এলাকায় কি আপনাদের ব্রাঞ্চ আছে", "আপনাদের ওখানে কতজন ডাক্তার",
    "আপনাদের ওখানে কি কিছু পাওয়া যায়", "আপনাদের কাছে কি ওষুধ পাওয়া যায়",
    # nothing about the clinic
    "আমি কোথায় যাব", "আমাকে একটা ট্যাক্সি ডাকুন", "ধন্যবাদ", "হ্যাঁ ঠিক আছে", "রাজু দাস", "না থাক",
    "", "   ",
]


@pytest.mark.parametrize("said", BRIEF)
def test_every_question_of_the_brief_is_the_address(said):
    assert asks(said) is True


def test_the_brief_is_ninety_questions():
    assert len(BRIEF) == 90 and len(set(BRIEF)) == 90


@pytest.mark.parametrize("said", PLAIN)
def test_the_plain_phrasings_agree(said):
    assert asks(said) is True


@pytest.mark.parametrize("said", NOT_THE_ADDRESS)
def test_what_is_not_a_question_about_where_the_clinic_is_is_left_alone(said):
    assert asks(said) is False


@pytest.mark.parametrize("said", ["where is the clinic", "आपका क्लिनिक कहाँ है", "how do I get there", "address please"])
def test_only_bengali_script_is_read(said):
    assert asks(said) is False


def test_an_ever_is_not_a_when():
    """"কখনও" (ever / never) starts like "কখন" (when) and must not be read as a time."""
    assert asks("আমি আগে কখনও আপনাদের ওখানে যাইনি, কীভাবে যাব?") is True
    assert asks("আপনাদের ওখানে কখন যাব") is False


# ================================================================== through the real orchestrator and clinic
def _location_answer(call) -> str:
    return call._run(call.m._tools.get_faq("location", "bn"))["answer"]


@pytest.mark.parametrize(
    "said,model_said,model_slots",
    [
        ("আপনারা কোথায় বসেন?", "doctor_availability", {"doctor_name": "আপনারা"}),  # was a doctor's schedule
        ("আপনাদের সেন্টারটা কি মেইন রোডের ওপর?", "clinic_faq", {"faq_topic": "contact_number"}),  # was the phone number
        ("আপনাদের কাছে যাওয়ার জন্য কোন গাড়ি পাব?", "clinic_faq", {"faq_topic": "parking"}),  # was parking
        ("আপনাদের ওখানে আসার কথা ছিল।", "unrelated_caller", {}),  # was "I did not understand"
        ("আপনাদের সেন্টারটা কীসের কাছে?", "smalltalk", {}),
    ],
)
def test_the_address_is_given_whatever_the_model_or_the_keywords_said(said, model_said, model_slots):
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))
    from _conversation import conversation

    with conversation("bn") as call:
        spoken = " ".join(call.say(said, model_said, dict(model_slots)))
        assert _location_answer(call) in spoken
        assert call.blocked == []


def test_a_doctors_schedule_question_is_still_a_doctors_schedule():
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))
    from _conversation import conversation

    with conversation("bn") as call:
        spoken = " ".join(call.say("ডাক্তার ভট্টাচার্য কোথায় বসেন", "doctor_availability", {"doctor_name": "ভট্টাচার্য"}))
        assert _location_answer(call) not in spoken


def test_an_open_booking_keeps_its_own_turns():
    """While a booking is waiting for an answer, a sentence like this is not hijacked into the address."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))
    from _conversation import conversation

    with conversation("bn") as call:
        call.say("আমি ডাক্তার ভট্টাচার্যকে দেখাতে চাই", "book_appointment", {"doctor_name": "ভট্টাচার্য"})
        assert call.session.booking is not None
        spoken = " ".join(call.say("আপনাদের ওখানে কোথায় যাব", "unclear", {}))
        assert _location_answer(call) not in spoken
