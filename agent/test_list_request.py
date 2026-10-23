"""Is the caller asking WHICH tests the clinic does -- the list, not one test's price or preparation?

story: "Caller asks which lab tests are available" (live 2026-10-07). "আপনাদের ওখানে কোন কোন ল্যাব টেস্ট হয়" has no
intent of its own in the model's schema: it came back `test_prep` with no test named, and the caller was asked
"are you a registered patient, or is this test booked?" -- the preparation flow's own first question. Hindi ("कौन
कौन से लैब टेस्ट") and English ("Which lab tests do you do?") went the same way.

Decided here, deterministically, before the model: the question is recognised by its shape -- a reduplicated or
plural "which / what" (কোন কোন, কী কী, कौन कौन, "which tests") or "all tests" / "list of tests" next to the word
for test. A single "which test" ("কোন টেস্টটা করতে হবে?" -- which test should I do?) is NOT a list request and is
left to the model. Pure: no I/O.

THE FOLLOW-UP ("shall I go on?"). While the list is being read, five tests a turn, the caller answers with
`wants_more` / `wants_repeat`. Callers say this in a hundred ways and in four writings of three languages -- Bengali
and Hindi script, English, and Bengali / Hindi typed or transliterated in Latin letters ("aro bolun", "porer gulo",
"baki gulo", "aur batao", "agle wale"). A recogniser that writes the second language in Latin letters, or a caller
who does, must not lose the list. The phrase lists below are the vocabulary; matching is by phrase, on word edges, with
repeated Latin letters collapsed ("aaro" == "aro", "baaki" == "baki") so spelling does not have to be guessed.

An utterance that is really another question ("তারপর বুক করব", "cbc er daam koto") is NOT "more": `_NOT_MORE`.
REASONED from how callers phrase this, not measured on call data.
"""

from __future__ import annotations

import re
import unicodedata

# The recogniser writes "test" many ways -- "टेस्ते" (the first words of a live Hindi call, 2026-10-14, were not taken for a
# list request and were answered with the clinic's address), "टेस्त", "टैस्ट", "টেস্ত" -- and any ending may follow.
_TEST = r"(?:টেস্ট|টেস্ত|টেষ্ট|টেসট|টেস্টিং|পরীক্ষা|পরিক্ষা|टेस्ट|टेस्त|टैस्ट|टेसट|टेस्टिंग|टेस्ते|जांच|जाँच|परीक्षण)"
# What the English recogniser writes when it mishears "tests" (live 2026-10-08: "which kind of tastes are there in
# your diagnostic centre" -> not a list request -> the model gave up). Plural forms only, like the pattern they feed.
_EN_TESTS = r"(?:tests|tastes|texts)"
_EN_TEST_ANY = r"(?:tests?|tastes?|texts?)"
_LIST_REQUEST = re.compile(
    "|".join(
        [
            # Bengali: কোন কোন / কী কী / কি কি / কোন সব / সব ... টেস্ট; টেস্টের লিস্ট / তালিকা
            rf"(?:কোন\s*কোন|কী\s*কী|কি\s*কি|কোন\s*সব|কী\s*ধরনের|কি\s*ধরনের|সব(?:গুলো)?)\s*(?:\S+\s+){{0,3}}?{_TEST}",
            rf"{_TEST}\S*\s*(?:এর\s*)?(?:লিস্ট|তালিকা|লিষ্ট)",
            # Hindi: कौन कौन से / कौन-कौन / क्या क्या / सारे / सभी ... टेस्ट; टेस्ट की लिस्ट / सूची
            rf"(?:कौन[\s-]*कौन|क्या[\s-]*क्या|किस[\s-]*किस|सारे|सभी|कौन\s*सी\s*सारी)\s*(?:\S+\s+){{0,3}}?{_TEST}",
            rf"{_TEST}\S*\s*(?:की|का|के)?\s*(?:लिस्ट|सूची)",
            # English
            rf"\b(?:which|what)\s+(?:all\s+|kind\s+of\s+|kinds\s+of\s+|sort\s+of\s+)?(?:lab\s+|blood\s+)?{_EN_TESTS}\b",
            rf"\blist\s+of\s+(?:the\s+|your\s+)?(?:lab\s+)?{_EN_TESTS}\b",
            rf"\b(?:all|your)\s+(?:the\s+)?(?:lab\s+)?{_EN_TESTS}\b",
            rf"\b{_EN_TEST_ANY}\s+(?:do\s+you|are)\s+(?:have|do|offer|available|done)\b",
        ]
    ),
    re.IGNORECASE,
)

# =============================================================================== the follow-up vocabulary
# Native scripts: phrase starts on a script boundary; a suffix may follow (পরের -> পরেরগুলো, বাকি -> বাকিগুলো).
_BN_MORE = (
    "আরও", "আরো", "আরেকটু", "আরেক টু", "আরেকটা", "আরেকটি", "আর কিছু", "আর কী কী", "আর কি কি", "আর কোন কোন",
    "আর কোনগুলো", "আর কোনটা", "আর কী আছে", "আর কি আছে", "আর আছে", "আর কয়টা", "আর কয়েকটা",
    "পরের", "পরেরটা", "পরেরগুলো", "পরের টেস্ট", "এরপর", "এর পর", "এরপরের", "এর পরের", "তারপর", "তার পরের", "তারপরে",
    "পরে কী", "পরে কি", "এর পরে", "পরবর্তী", "নেক্সট", "নেক্সটটা", "নেক্স্ট",
    "বাকি", "বাকী", "বাকিটা", "বাদবাকি", "অন্যগুলো", "অন্য টেস্ট", "অন্যান্য", "বাকি সব", "বাকি টেস্ট",
    "বলে যান", "বলে যাও", "বলতে থাকুন", "বলতে থাকেন", "বলতে থাকো", "চালিয়ে যান", "চালিয়ে যাও", "চালিয়ে যেতে",
    "এগিয়ে যান", "থামবেন না", "শুনতে চাই", "শুনব", "শুনবো", "শুনি", "শুনতে পারি", "শুনতে পারব", "শুনতে চাইছি",
    "সবগুলো বলুন", "সব বলুন", "সবটা বলুন", "পুরোটা বলুন", "পুরো তালিকা", "সম্পূর্ণ তালিকা", "সব টেস্ট বলুন",
    "মোর", "কন্টিনিউ", "গো অন", "রেস্ট",
)
_HI_MORE = (
    "और बताइए", "और बताइये", "और बताओ", "और सुनाइए", "और सुनाइये", "और सुनाओ", "और सुनना", "और सुनेंगे", "और भी",
    "और बोलिए", "और बोलिये", "और बोलो", "और बोलें", "और बोलिएगा", "और बताएं", "और बताएँ", "और बताईये", "और कहिए", "और कहो",
    "और सुनाएं", "और सुनाएँ", "और बताना", "और बोलना", "आगे बोलिए", "आगे बोलो", "आगे कहिए", "बोलते जाइए", "बोलते रहिए",
    "बोलते जाओ", "बताते चलिए", "सुनाते जाइए", "और है क्या", "और क्या है", "कुछ और", "बाकी बोलिए", "बाकी बताइए",
    "और कौन कौन", "और क्या क्या", "और कुछ", "और है", "और हैं", "और कौन से", "और कितने",
    "आगे", "आगे बताइए", "आगे बताओ", "आगे के", "आगे बढ़िए", "आगे बढ़ो",
    "अगले", "अगला", "अगली", "अगले वाले", "अगले टेस्ट", "अगले पाँच", "अगले पांच",
    "बाकी", "बाकी के", "बाकी सब", "बाकियों", "बाकी वाले", "बाकी टेस्ट",
    "जारी रखिए", "जारी रखो", "बताते जाइए", "बताते रहिए", "बताते जाओ", "बोलते रहिए",
    "पूरी लिस्ट", "सारे बताइए", "सब बताइए", "सुनाइए", "सुनाओ", "सुनूँगा", "सुनूंगा", "सुनना है", "सुनना चाहता", "सुनना चाहती",
    "नेक्स्ट", "मोर", "कंटिन्यू",
)
_EN_MORE = (
    "more", "next", "next ones", "next few", "next five", "go on", "go ahead", "continue", "carry on", "keep going",
    "keep on", "proceed", "the rest", "rest of them", "rest of the tests", "remaining", "what else", "anything else",
    "anything more", "any more", "is there more", "are there more", "others", "other ones", "other tests",
    "tell me more", "read more", "read on", "read the next", "and then", "what's next", "whats next", "what comes next",
    "after that", "give me more", "show me more", "more tests", "few more", "some more", "another five", "and the rest",
)
# Latin letters, Bengali (Banglish). Repeated letters are collapsed on both sides, so write the plain spelling.
_BN_MORE_LATIN = (
    "aro", "aro bolun", "aro bolen", "aro bolo", "aro kichu", "aro kichu bolun", "aro ache", "aro ki ki ache",
    "aro koyta", "aro koyekta", "aro shunbo", "aro sunbo", "aro shunte chai", "aro sunte chai", "aro shunbo",
    "ar kichu", "ar ki ki", "ar ki ki ache", "ar kon kon", "ar ache", "ar ki ache", "ar koyta", "arekta", "arektu",
    "porer", "porer gulo", "porergulo", "porer ta", "porerta", "porer test", "porer testgulo", "porer test gulo",
    "porer gulo bolun", "porergulo bolun", "pore ki", "er por", "erpor", "er porer", "erporer", "tarpor", "tarpore",
    "tar porer", "porobortee", "porobarti", "nexto", "next ta", "next ta bolun", "next gulo", "next gulo bolun",
    "baki", "bakigulo", "baki gulo", "baki gulo bolun", "baki test", "bakita", "baki sob", "bakigulo bolun",
    "badbaki", "onnogulo", "onno gulo", "onno test", "onnano", "bole jan", "bole jao", "bolte thakun",
    "bolte thako", "chalie jan", "chalie jao", "egie jan", "thamben na", "shunbo", "sunbo", "shunte chai",
    "sunte chai", "shuni", "suni", "shunte pari", "sob bolun", "sobgulo bolun", "sob gulo bolun", "purota bolun",
    "puro talika", "full list", "shob bolun",
)
_HI_MORE_LATIN = (
    "aur", "aur batao", "aur bataiye", "aur bataiyen", "aur bataye", "aur sunao", "aur sunaiye", "aur sunaiyen",
    "aur sunna", "aur sununga", "aur sunenge", "aur bhi", "aur kuch", "aur kya", "aur kya kya", "aur kya kya hai",
    "aur hai kya", "aur kaun kaun", "aur kaun se", "aur kitne", "aage", "aage batao", "aage bataiye", "aage ke",
    "aage badhiye", "aage badho", "agle", "agla", "agli", "agle wale", "agle test", "agle paanch", "agle panch",
    "baki", "baki ke", "baki sab", "baki wale", "baki test", "bakiyon", "jaari rakho", "jaari rakhiye",
    "batate jao", "batate jaiye", "batate raho", "batate rahiye", "poori list", "pura list", "saare batao",
    "sab batao", "sab bataiye", "sunao", "sunaiye", "next batao", "next wale",
)
# Whole utterances that are "go on" only because a list is open and the agent just asked.
_MORE_WHOLE = (
    "বলুন", "বলেন", "বলো", "বল", "চলুক", "চালান", "শুনুন", "ঠিক আছে বলুন", "হ্যাঁ বলুন", "বলুন না", "বলেন না",
    "बोलिए", "बोलो", "बताइए", "बताओ", "बताइये", "चलिए", "चलो", "आगे चलिए", "ठीक है बताइए",
    "bolun", "bolen", "bolo", "bolun na", "bolen na", "bol", "chalun", "cholun", "chalu korun", "ok bolun", "thik ache bolun",
    "boliye", "batao", "bataiye", "batao na", "chaliye", "chalo", "theek hai batao", "theek hai bataiye",
    "go", "go on please", "ok go on", "okay go on", "yes more", "yes go on", "yes please", "please", "ok", "okay",
)

# "Say that again" -- the page just read, once more. (A price asked for by NAME is not this: it is a question.)
_BN_REPEAT = (
    "আবার বলুন", "আবার বলেন", "আবার বলো", "আবার বল", "আবার শুনি", "আবারও বলুন", "আরেকবার", "আরেক বার", "ফের বলুন",
    "রিপিট", "আবার", "শুনিনি", "শুনি নি", "শুনতে পাইনি", "শুনতে পাই নি", "বুঝতে পারিনি", "বুঝতে পারিনি", "বুঝলাম না", "কী বললেন", "কি বললেন",
    "কী বলেছিলেন", "কি বলেছিলেন", "কত বলেছিলেন", "কত বললেন", "কী যেন বললেন", "কত যেন বললেন", "আবার বলবেন",
    "ঠিক শুনতে পাইনি", "পুরোটা আবার", "আগেরগুলো", "আগেরটা", "আগের", "ফিরে যান", "আগে কী বললেন",
)
_HI_REPEAT = (
    "दोबारा", "फिर से", "फिर से बताइए", "फिर से बताओ", "एक बार और", "दोबारा बताइए", "दोबारा बताओ", "क्या कहा",
    "क्या बताया", "कितना बताया", "कितना बोला", "क्या बोला", "सुनाई नहीं दिया", "सुनाई नहीं पड़ा", "समझ नहीं आया",
    "रिपीट", "पिछले वाले", "पहले वाले", "फिर बताइए",
)
_EN_REPEAT = (
    "say again", "say that again", "repeat", "repeat that", "repeat please", "again please", "once more", "one more time",
    "come again", "pardon", "what did you say", "what was that", "what did you just say", "sorry what", "can you repeat",
    "could you repeat", "how much did you say", "i didn't hear", "i did not hear", "didn't catch", "did not catch",
    "the previous ones", "go back", "previous", "again",
)
_REPEAT_LATIN = (
    "abar bolun", "abar bolen", "abar bolo", "abar bol", "abar shuni", "abar", "arekbar", "arek bar", "arekbar bolun",
    "arekbar bolen", "ki bolechilen", "ki bollen", "ki bolle", "ki bolechen", "koto bolechilen", "koto bollen",
    "koto bolechen", "kto bolechilen", "koto jeno bollen", "shunte pai ni", "shunte paini", "shunte pai nai",
    "bujhte parini", "bujhte pari ni", "bujhlam na", "agergulo", "agerta", "ager gulo", "ager ta", "ager", "repeat korun",
    "dobara", "dobara batao", "dobara bataiye", "phir se", "phir se batao", "phir se bataiye", "ek baar aur",
    "kya kaha", "kya bola", "kya bataya", "kitna bataya", "kitna bola", "kitna kaha", "sunai nahi diya",
    "samajh nahi aaya", "pichle wale", "pehle wale", "phir bataiye",
)

# An utterance that carries a question of its own is not "more": a price, a booking, a time, a place, a doctor.
_NOT_MORE_NATIVE = (
    "বুক", "বুকিং", "অ্যাপয়েন্টমেন্ট", "ডাক্তার", "ডক্টর", "দাম", "রেট", "টাকা", "কত", "ফি ", "প্রস্তুতি", "খালি পেটে",
    "ঠিকানা", "সময়", "কখন", "খোলা", "বন্ধ", "কোথায়", "রিপোর্ট",
    "बुक", "अपॉइंटमेंट", "डॉक्टर", "दाम", "रेट", "कितना", "कितने", "रुपये", "पता", "टाइम", "कब", "कहाँ", "रिपोर्ट",
)
_NOT_MORE_LATIN = (
    "book", "booking", "appointment", "doctor", "price", "rate", "cost", "how much", "daam", "dam", "koto", "kato",
    "taka", "fee", "address", "timing", "time", "when", "where", "report", "kitna", "kitne", "kab", "kahan", "paisa",
    "rupee", "rupees", "kon doctor", "kon time",
)

_LATIN_RUN = re.compile(r"([a-z])\1+")
_NOISE = re.compile(r"[।?!,.;:'\"()\-–—/|]+")
_WS = re.compile(r"\s+")


def _collapse(latin: str) -> str:
    return _LATIN_RUN.sub(r"\1", latin)


def _norm(text: str) -> str:
    """NFC, lower-cased, punctuation out, repeated Latin letters collapsed ("aaro" -> "aro")."""
    t = unicodedata.normalize("NFC", text or "").lower()
    t = _WS.sub(" ", _NOISE.sub(" ", t)).strip()
    return _collapse(t)


def _latin_alternation(phrases: tuple[str, ...]) -> str:
    out = []
    for p in sorted({_collapse(x.lower()) for x in phrases}, key=len, reverse=True):
        out.append(r"\s+".join(re.escape(w) for w in p.split()))
    return "(?:" + "|".join(out) + ")"


def _native_alternation(phrases: tuple[str, ...]) -> str:
    out = []
    for p in sorted({unicodedata.normalize("NFC", x).strip() for x in phrases}, key=len, reverse=True):
        out.append(r"\s*".join(re.escape(w) for w in p.split()))
    return "(?:" + "|".join(out) + ")"


_L = "ঀ-৿ऀ-ॿ"  # Bengali and Devanagari blocks: a phrase starts where no letter of either precedes it
_ALL_NATIVE_MORE = _BN_MORE + _HI_MORE
_MORE_NATIVE_RE = re.compile(r"(?<![" + _L + r"])" + _native_alternation(_ALL_NATIVE_MORE))
_MORE_LATIN_RE = re.compile(
    r"(?<![a-z])" + _latin_alternation(_EN_MORE + _BN_MORE_LATIN + _HI_MORE_LATIN) + r"(?![a-z])"
)
_MORE_WHOLE_SET = {_norm(x) for x in _MORE_WHOLE}

_REPEAT_NATIVE_RE = re.compile(r"(?<![" + _L + r"])" + _native_alternation(_BN_REPEAT + _HI_REPEAT))
_REPEAT_LATIN_RE = re.compile(r"(?<![a-z])" + _latin_alternation(_EN_REPEAT + _REPEAT_LATIN) + r"(?![a-z])")

_NOT_MORE_NATIVE_RE = re.compile("|".join(re.escape(unicodedata.normalize("NFC", x)) for x in _NOT_MORE_NATIVE))
_NOT_MORE_LATIN_RE = re.compile(r"(?<![a-z])" + _latin_alternation(_NOT_MORE_LATIN) + r"(?![a-z])")

# Hindi "और" is also "and" ("सीबीसी और थायराइड बुक करना है" names two tests): on its own, or in the combinations above.
_HI_AUR_ALONE = re.compile(r"^\s*और\s*$")


def asks_for_test_list(text: str) -> bool:
    return bool(_LIST_REQUEST.search(unicodedata.normalize("NFC", text or "")))


def _carries_a_question_of_its_own(t: str) -> bool:
    return bool(_NOT_MORE_NATIVE_RE.search(t) or _NOT_MORE_LATIN_RE.search(t))


def wants_more(text: str, *, bare: bool = True) -> bool:
    """"Go on" / "the next ones" / "the rest" -- in Bengali, Hindi or English script or in Latin-letter Bengali or
    Hindi. False for an utterance that is really a question (a price, a booking, a time) even if it has such a word.

    `bare=False` leaves out the one-word answers ("ok", "বলুন", "और", "please"): right while the agent has just asked
    "shall I go on?", wrong several turns later, when "ok" is only an acknowledgement."""
    t = _norm(text)
    if not t:
        return False
    if bare and (_HI_AUR_ALONE.match(t) or t in _MORE_WHOLE_SET):
        return True
    if _carries_a_question_of_its_own(t):
        return False
    return bool(_MORE_NATIVE_RE.search(t) or _MORE_LATIN_RE.search(t))


def wants_repeat(text: str) -> bool:
    """"Say that again" -- the page just read, once more. The caller decides whether a TEST was named in it (then it is
    a price question and not this)."""
    t = _norm(text)
    if not t:
        return False
    return bool(_REPEAT_NATIVE_RE.search(t) or _REPEAT_LATIN_RE.search(t))
