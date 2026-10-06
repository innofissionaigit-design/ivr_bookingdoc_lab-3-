"""A caller who is clearly NOT asking for anything from the hospital: a wrong number, a chat meant for a friend, a request
for a person by name. Decided HOSPITAL-FIRST, deterministically where the words are unmistakable, and never by guessing.

# ADDED BY SOURAV: story "Caller has reached the hospital by mistake" (Difficult, Sensitive and Edge Cases).
#
# WHY THIS MODULE EXISTS. The orchestrator already had `smalltalk` (a catch-all that keeps the call open and may speak a
# model-written reply) and `unclear` ("I did not understand you"). Neither means "this person does not want us", so a
# wrong-number caller was greeted again and again until the silence timer ended the call, and the call was filed as
# `completed`, which looks exactly like a call the hospital successfully served. This module is the missing concept, and it
# is deliberately small.
#
# THE THREE RULES IT KEEPS (they come straight from the story):
#
#   1. HOSPITAL ALWAYS WINS. `has_hospital_cue()` is the veto every other function here goes through. It reuses the
#      per-language cue tables FastPath already owns (rate / availability / booking / preparation words, in bn, hi and en)
#      plus a SMALL list of plain hospital nouns, and the caller (main.py) adds the live catalogue (a test name, an FAQ
#      topic, a full doctor name) through `entity_match`. A casual sentence that carries any of them is a hospital request:
#      "bhai kaise ho, CBC ka rate kya hai", "sorry wrong number, actually I wanted my report".
#   2. A NAME ON ITS OWN IS NOT A PATIENT, A DOCTOR OR A BOOKING. "Suresh hai kya?" is a person being asked for. It is
#      recognised by its SHAPE (a word + "hai kya" / "se baat karwao" / "ache?"), never by looking the name up. "Dr Suresh ka
#      appointment chahiye" has a hospital cue and an explicit doctor word, so it is never touched.
#   3. NOTHING HERE ASKS FOR OR STORES ANYTHING. It returns a label; the words spoken are fixed, reviewed phrases
#      (agent/phrases.py `unrelated_first` / `unrelated_final`); no caller text leaves this module.
#
# WHAT IS DETERMINISTIC AND WHAT IS NOT. Only two unmistakable families are decided here: "wrong number / wrong place" and
# "a named person is being asked for". Everything softer ("arre bhai kaise ho", weather, office talk) is left to the new
# `unrelated_caller` intent the language model returns (agent/llm.py), and `llm_verdict()` below double-checks that answer
# against the same hospital veto before the call is allowed to treat it as unrelated. A plain greeting or thanks is NOT
# unrelated: it stays `smalltalk`.
#
# REASONED, not measured, like agent/abuse.py, agent/call_end.py and agent/human_request.py: the cue lists were written by a
# non-native speaker, kept short on purpose, and need a native reviewer (docs/pod-verification-checklist.md).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from agent import fast_path_cues

# ---- result labels ------------------------------------------------------------------------------------------------
WRONG_NUMBER = "wrong_number"
PERSON_DIRECTED = "person_directed"

# What the language-model answer is judged as by llm_verdict().
VERDICT_UNRELATED = "unrelated"  # nothing hospital in it: treat as an unrelated caller
VERDICT_GREETING = "greeting"  # a plain hello / thanks: stays smalltalk
VERDICT_HOSPITAL = "hospital"  # a hospital cue or slot is present: NOT unrelated

# The caller is closed on this many CONSECUTIVE unrelated turns (the story: "closed politely within two turns").
CLOSE_AT = 2

# An utterance longer than this many words is never decided deterministically: a long sentence has room for a real request.
_MAX_WORDS = 8

_PUNCT = re.compile(r"[?!.,;:()\"“”‘’|/\\।…]+")


def _norm(text: str) -> str:
    """NFC, nukta dropped (the same rule agent/fast_path_cues.normalise_cue applies, so a cue matches in either Unicode form),
    lower case, punctuation turned into spaces, whitespace collapsed. Apostrophes are kept ("don't")."""
    t = unicodedata.normalize("NFC", text or "").replace("़", "").lower()
    return " ".join(_PUNCT.sub(" ", t).split())


# ---- wrong number / wrong place ----------------------------------------------------------------------------------------
# Latin script (English, Hinglish, Banglish): regexes on word boundaries.
_WN_LATIN_TIGHT = tuple(
    re.compile(p)
    for p in (
        r"\bwrong (number|no|nbr|call|place|person|hospital|clinic|department)\b",
        r"\b(galat|ghalat|galt|rong|wrong) (number|nombor|nambar|jagah|jagha|jaga|call|phone|fone|hospital|jayga|jaygay)\b",
        r"\bnumber (galat|ghalat|wrong)\b",
        r"\b(galti|bhul|bhool|vul)\s*(se|kore|korey)\b.{0,20}\b(phone|call|fone|dial|laga|lag|kar|kor|hoye|hoe)\w*",
        r"\b(bhul|vul|bhool)\s+(number|nombor|nambar|jayga|jaygay|jaiga|call|phone|fone)\b",
        r"\b(dialled|dialed|dialling|dialing|called|rang|phoned|reached)\b.{0,15}\bwrong\b",
        r"\bmis-?dial(l)?(ed)?\b",
        r"\bdidn'?t mean to (call|dial|phone|ring)\b",
    )
)
# LOOSE cues span a stretch of free words ("I thought my test number..."), so they are only DETECTED, never cut out: the hospital
# veto then runs on the whole sentence and a real word inside the stretch ("test", "report") still wins.
_WN_LATIN_LOOSE = tuple(
    re.compile(p)
    for p in (
        r"\b(call|called|dial|dialled|dialed|phone|phoned|rang)\w*\b.{0,25}\bby mistake\b",
        r"\bby mistake\b.{0,25}\b(call|dial|phone|ring)",
        # "I thought this was Suresh's number"
        r"\b(thought|assumed|presumed|figured)\b.{0,30}\b(number|phone)\b",
        # "mujhe laga ye Suresh ka number hai", "socha tha Rakesh ka number hai"
        r"\b(laga|lagaa|lagta|samjha|samjhi|socha|sochi|bhabchilam|bhebechilam|bhebechhilam)\b.{0,40}\b(ka|ki|ke|er|r) ?(phone )?(number|nambar|nombor)\b",
        # "bhai ye Rakesh ka number hai kya"
        r"\b(ye|yeh|yah|eta|eti)\b\s+[a-z]+\s+(ka|ki|er) (phone )?(number|nambar|nombor)( hai| he| ki)?( kya| na)?$",
    )
)
# Devanagari / Bengali script: plain substrings (both languages inflect a word's ending, so a cue sits inside the longer word).
_WN_NATIVE = (
    # Hindi
    "गलत नंबर",
    "गलत नम्बर",
    "गलत नमबर",
    "नंबर गलत",
    "नम्बर गलत",
    "गलत जगह",
    "गलत कॉल",
    "गलत फोन",
    "गलत फ़ोन",
    "गलती से फोन",
    "गलती से फ़ोन",
    "गलती से कॉल",
    "गलती से लग",
    "रॉन्ग नंबर",
    "रॉंग नंबर",
    "रांग नंबर",
    "रॉन्ग नम्बर",
    # Bengali
    "ভুল নম্বর",
    "ভুল নাম্বার",
    "ভুল নাম্বারে",
    "ভুল ফোন",
    "ভুল করে ফোন",
    "ভুল করে কল",
    "ভুল জায়গা",
    "ভুল কল",
    "ভুলে ফোন",
    "রং নম্বর",
    "রং নাম্বার",
    "রং কল",
    "রং জায়গা",
)
_WN_NATIVE_RE = tuple(
    re.compile(p)
    for p in (
        # Hindi: "मुझे लगा ये सुरेश का नंबर है"
        r"(लगा|समझा|सोचा|समझी|सोची).{0,40}(का|की|के) (फोन )?(नंबर|नम्बर)",
        # Hindi: "ये राकेश का नंबर है क्या"
        r"(ये|यह|यही)\s+\S+\s+का (फोन )?(नंबर|नम्बर)( है| हैं)?( क्या| ना)?$",
        # Bengali: "আমি ভেবেছিলাম এটা সুরেশের নম্বর"
        r"(ভেবেছিলাম|ভেবেছিলুম|মনে করেছিলাম|মনে হয়েছিল|ভেবেছিলেম).{0,40}(নম্বর|নাম্বার)",
    )
)

# ---- a named person is being asked for -----------------------------------------------------------------------------------
# Words that may sit in front of the name ("bhai", "arre", "hello"...). They carry no meaning for this decision.
_FILLER = (
    r"bhai|bhaiya|bro|brother|arre|are|arey|oh|o|dada|dadu|didi|bhaiyya|hello|hi|hey|sorry|ji|sir|madam|please|plz|achha|accha|"
    r"acha|haan|han|ha|ok|okay|ei|oi|aye"
)
_FILLER_NATIVE = (
    r"भाई|भैया|अरे|ओ|दादा|दीदी|हैलो|हेलो|सॉरी|जी|सर|प्लीज|अच्छा|हाँ|हां|"
    r"দাদা|দাদু|দিদি|ভাই|ওরে|এই|হ্যালো|সরি|আচ্ছা|হ্যাঁ|হ্যা"
)

# A word in the "name" position that is NOT a person: a pronoun, a question word, someone-in-general, or a facility/test noun
# that a real hospital caller asks "is there...?" about ("parking hai kya", "ambulance ache?"). Kept small; the cue tables and the
# catalogue veto catch the rest.
_NOT_A_NAME = frozenset(
    {
        # pronouns / question words / "someone"
        "koi", "kisi", "kaun", "kya", "kuch", "wo", "woh", "vo", "ye", "yeh", "yah", "yaha", "yahan", "vahan", "wahan", "aap",
        "aapka", "apka", "tum", "tumhara", "sab", "kahin", "who", "what", "someone", "anyone", "somebody", "anybody", "everyone",
        "person", "human", "ki", "ke", "kemon", "kothay", "keu", "kew", "eta", "ota", "ekhane", "apni", "tumi", "ami", "mera",
        "sir", "madam", "ji", "bhai", "dada", "didi",  # a bare kinship word with nothing after it names no one in particular
        # people-in-the-hospital words (also hospital cues, listed so a stray cue-table miss never makes them a "name")
        "manager", "staff", "reception", "receptionist", "operator", "doctor", "dr", "nurse", "sister", "daktar", "dactar",
        # facilities / services a hospital caller really asks about
        "parking", "pharmacy", "medicine", "dawai", "dawa", "canteen", "ambulance", "lift", "ward", "bed", "icu", "opd", "ecg",
        "xray", "mri", "ct", "scan", "ultrasound", "insurance", "lab", "blood", "sample", "test", "report", "collection",
        "emergency", "timing", "time", "address", "location", "washroom", "toilet", "atm", "wifi",
        "कोई", "किसी", "कौन", "क्या", "कुछ", "वो", "वह", "ये", "यह", "यहाँ", "यहां", "आप", "तुम", "सब", "कहीं", "भाई", "दादा", "दीदी",
        "पार्किंग", "फार्मेसी", "दवा", "दवाई", "एम्बुलेंस", "एंबुलेंस", "लिफ्ट", "वार्ड", "बेड", "आईसीयू", "एक्सरे", "स्कैन", "बीमा",
        "कেউ", "কেউ", "কোনো", "কে", "কী", "কি", "এটা", "ওটা", "এখানে", "আপনি", "তুমি", "সব", "কেমন", "কোথায়", "কখন", "দাদা", "ভাই", "দিদি",
        "পার্কিং", "ফার্মেসি", "ওষুধ", "অ্যাম্বুলেন্স", "লিফট", "ওয়ার্ড", "বেড", "আইসিইউ", "এক্স-রে", "স্ক্যান", "বীমা",
    }
)  # fmt: skip


def _person_patterns() -> tuple[re.Pattern, ...]:
    f = rf"(?:(?:{_FILLER})\s+){{0,3}}"
    fn = rf"(?:(?:{_FILLER_NATIVE})\s+){{0,3}}"
    return (
        # Hindi / Hinglish: "Suresh hai kya", "Bhai Suresh hai na"
        re.compile(rf"^{f}(?P<name>[a-z]+)\s+(?:hai|he|h|hain)\s+(?:kya|na|ji)$"),
        # "Amit se baat karwao", "Amit se baat karni hai"
        re.compile(
            rf"^{f}(?P<name>[a-z]+)\s+se\s+(?:baat|bat|baatcheet)\s*(?:karwa|karva|karao|kara|karna|karni|kijiye|karwana|karvana)\w*(?:\s+(?:do|dijiye|hai|tha|thi))?$"
        ),
        # "Ramesh ko phone diya tha"
        re.compile(
            rf"^{f}(?P<name>[a-z]+)\s+ko\s+(?:phone|fon|call)\s+(?:diya|dena|do|de do|dijiye|dedo)(?:\s+(?:tha|thi|hai))?$"
        ),
        # Banglish: "Suresh ache?", "Dada Suresh ache ki"
        re.compile(rf"^{f}(?P<name>[a-z]+)\s+(?:ache|achhe|achen|achhen|ase)(?:\s+ki)?$"),
        # English: "Is Suresh there", "Can I speak to Suresh please", "Put Suresh on the phone"
        re.compile(rf"^{f}(?:is|are)\s+(?P<name>[a-z]+)\s+(?:there|in|around|home|at home)$"),
        re.compile(
            rf"^{f}(?:(?:can|may) i |let me |please |i want to |i wanna |i need to )?(?:talk|speak|connect)\s+(?:to|with)\s+(?P<name>[a-z]+)(?:\s+please)?$"
        ),
        re.compile(rf"^{f}(?:put|get)\s+(?P<name>[a-z]+)\s+on(?:\s+the)?\s+(?:phone|line)$"),
        # Devanagari: "सुरेश है क्या", "अमित से बात करवाओ", "रमेश को फोन दिया था"
        re.compile(rf"^{fn}(?P<name>\S+)\s+(?:है|हैं)\s+(?:क्या|ना|जी)$"),
        re.compile(
            rf"^{fn}(?P<name>\S+)\s+से\s+बात\s+(?:करवाओ|करवा दो|कराओ|करा दो|करवाइए|कराइए|करनी है|करना है|करवा दीजिए)$"
        ),
        re.compile(rf"^{fn}(?P<name>\S+)\s+को\s+(?:फोन|फ़ोन)\s+(?:दिया|दो|दे दो|देना)(?:\s+(?:था|थी|है))?$"),
        # Bengali: "সুরেশ আছে?", "দাদা সুরেশ আছে কি", "সুরেশের সাথে কথা বলিয়ে দিন"
        re.compile(rf"^{fn}(?P<name>\S+)\s+আছে\s*(?:কি|কী|না)?$"),
        re.compile(
            rf"^{fn}(?P<name>\S+?)(?:র|এর|কে)?\s+(?:সাথে|সঙ্গে)\s+কথা\s+(?:বলিয়ে দিন|বলিয়ে দাও|বলাও|বলাবেন|বলতে চাই|বলব|বলতে দিন)$"
        ),
    )


_PERSON_PATTERNS = _person_patterns()

# ---- the hospital veto ---------------------------------------------------------------------------------------------------
# Plain nouns that mean "this is about the hospital". SMALL on purpose: the real vocabulary lives in FastPath's cue tables and
# the live catalogue, which `has_hospital_cue()` consults first. These are only the common words those tables do not carry,
# in each script a caller's transcript can arrive in.
_HOSPITAL_WORDS_LATIN = re.compile(
    r"\b(tests?|reports?|sample|samples|appointments?|bookings?|booked|doctors?|dr|daktar|dactar|hospital|clinic|lab|pathology|scan|"
    r"x-?ray|ultrasound|ecg|mri|blood|sugar|thyroid|cbc|lipid|urine|medicine|dawai|prescription|rate|rates|price|cost|fees?|kitna|"
    r"kitne|daam|dam|kimat|koto|collect|collection|pincode|parking|pharmacy|ambulance|insurance|timing|timings|opd|icu|ward|"
    r"complaint|refund|bill|payment|cancel|reschedule|confirmation|janch|jaanch|parikkha|checkup|check-up|fasting|home sample)\b"
)
_HOSPITAL_WORDS_NATIVE = (
    "टेस्ट",
    "रिपोर्ट",
    "डॉक्टर",
    "डाक्टर",
    "अस्पताल",
    "हॉस्पिटल",
    "क्लिनिक",
    "लैब",
    "सैंपल",
    "सैम्पल",
    "जांच",
    "जाँच",
    "खून",
    "स्कैन",
    "दवा",
    "पार्किंग",
    "एम्बुलेंस",
    "एंबुलेंस",
    "बीमा",
    "घर से",
    "कैंसिल",
    "टेस्ट",
    "বাড়ি থেকে",
    "টেস্ট",
    "রিপোর্ট",
    "ডাক্তার",
    "হাসপাতাল",
    "হসপিটাল",
    "ক্লিনিক",
    "ল্যাব",
    "স্যাম্পেল",
    "পরীক্ষা",
    "রক্ত",
    "স্ক্যান",
    "ওষুধ",
    "পার্কিং",
    "অ্যাম্বুলেন্স",
    "বীমা",
    "ক্যানসেল",
    "ক্যান্সেল",
)

EntityMatch = Callable[[str, str], bool]


def _cue_table_hit(norm: str) -> bool:
    """FastPath's own cue words (rate / availability / booking / preparation), checked in EVERY language: a caller code-switches,
    and the veto must be at least as wide as what FastPath would answer."""
    for lang in fast_path_cues.languages():
        table = fast_path_cues.table_for(lang)
        if table is not None and table.any(norm, table.rate + table.avail + table.book + table.prep):
            return True
    return False


def has_hospital_cue(text: str, lang: str = "en", entity_match: EntityMatch | None = None) -> bool:
    """True if ANYTHING in the utterance points at the hospital. The single veto the story's first rule needs.

    `entity_match(normalised_text, lang) -> bool` is supplied by the orchestrator and answers "does the live catalogue know a test,
    an FAQ topic or a full doctor name in this?". It is injected, not imported, because nothing under agent/ may import the
    orchestrator (CLAUDE.md, the one-import rule). A failure inside it is treated as "yes, hospital": an error must never be the
    reason a hospital caller is turned away.
    """
    norm = _norm(text)
    if not norm:
        return False
    if _HOSPITAL_WORDS_LATIN.search(norm) or any(w in norm for w in _HOSPITAL_WORDS_NATIVE) or _cue_table_hit(norm):
        return True
    if entity_match is not None:
        try:
            return bool(entity_match(norm, lang))
        except Exception:  # noqa: BLE001 - see the docstring: an error is a hospital caller, never a rejection
            return True
    return False


def is_plain_greeting(text: str, lang: str = "en") -> bool:
    """A bare hello / namaste / thanks (the same test and four-word limit FastPath's greeting tier uses). These are `smalltalk`."""
    norm = fast_path_cues.normalise_cue(text)
    if not norm or len(norm.split()) > 4:
        return False
    for lg in dict.fromkeys((lang, "en", "hi", "bn")):
        table = fast_path_cues.table_for(lg)
        if table is not None and (table.any(norm, table.greeting) or table.any(norm, table.thanks)):
            return True
    return False


def _strip_wrong_number(norm: str) -> tuple[bool, str]:
    """(is there a wrong-number phrase, the text to run the hospital veto on). The TIGHT phrases ("wrong number", "गलत नंबर", "wrong
    hospital") are cut out, so the apology's own words are not mistaken for a hospital cue. LOOSE cues ("I thought it was Suresh's
    number") only set the flag and cut nothing: a real hospital word inside them must still veto."""
    found = False
    rest = norm
    for rx in _WN_LATIN_TIGHT:
        m = rx.search(rest)
        if m:
            found = True
            rest = f"{rest[: m.start()]} {rest[m.end() :]}"
    for cue in _WN_NATIVE:
        if cue in rest:
            found = True
            rest = rest.replace(cue, " ")
    if not found:
        found = any(rx.search(norm) for rx in _WN_LATIN_LOOSE + _WN_NATIVE_RE)
    return found, " ".join(rest.split())


def _person_match(norm: str) -> bool:
    for rx in _PERSON_PATTERNS:
        m = rx.match(norm)
        if m:
            name = m.group("name")
            if len(name) >= 2 and not name.isdigit() and name not in _NOT_A_NAME:
                return True
    return False


def classify(text: str, lang: str = "en", entity_match: EntityMatch | None = None) -> str | None:
    """`WRONG_NUMBER`, `PERSON_DIRECTED`, or None. None means "not decided here": the turn goes on to FastPath and the model exactly as
    before. Hospital-first: any hospital cue (outside the apology's own words) returns None."""
    norm = _norm(text)
    if not norm or len(norm.split()) > _MAX_WORDS + 4:
        return None
    is_wrong_number, rest = _strip_wrong_number(norm)
    if is_wrong_number:
        # the apology's own words are removed first; what is left must carry no hospital cue ("sorry wrong number, actually my report")
        if rest and has_hospital_cue(rest, lang, entity_match):
            return None
        return WRONG_NUMBER
    if len(norm.split()) > _MAX_WORDS:
        return None
    if _person_match(norm) and not has_hospital_cue(norm, lang, entity_match):
        return PERSON_DIRECTED
    return None


def llm_verdict(text: str, lang: str, slots: dict | None, entity_match: EntityMatch | None = None) -> str:
    """Judge the language model's `unrelated_caller` answer BEFORE acting on it. A 7B model can be argued into anything, so its
    answer is only accepted when nothing in the turn contradicts it:

      * any filled slot (a test, a doctor, a date...) means the model SAW a hospital request -> VERDICT_HOSPITAL;
      * any hospital cue (cue tables, plain nouns, catalogue) -> VERDICT_HOSPITAL;
      * a bare greeting / thanks -> VERDICT_GREETING (it stays `smalltalk`);
      * otherwise VERDICT_UNRELATED.
    """
    if any(v not in (None, "", [], {}) for v in (slots or {}).values()):
        return VERDICT_HOSPITAL
    wrong, rest = _strip_wrong_number(_norm(text))
    if has_hospital_cue(rest if wrong else text, lang, entity_match):
        return VERDICT_HOSPITAL
    if is_plain_greeting(text, lang):
        return VERDICT_GREETING
    return VERDICT_UNRELATED
