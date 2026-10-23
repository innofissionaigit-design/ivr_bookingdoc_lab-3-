"""Which of the clinic's doctors does a caller's sentence mention?

The doctor lookup (`_doctor_exact_matches` / `_find_doctor_candidates` in main.py) answers "who is THIS name?" and is
careful about it. What it cannot do is find the name inside a whole sentence, and the agent had been handing it
whatever the intent model copied into the doctor slot. A real call on 2026-10-07 shows what that costs:

    caller: অন্য কোন কোন সময় বসেন উনি        ("what other times does he sit?")
    model : doctor_name = "বসেন"                ("sits")
    reply : 'বসেন' নামে ডাক্তার খুঁজে পাইনি। আপনি কি সেন বলতে চাইছেন?

So this module reads the WHOLE utterance against the clinic's own doctor table, the same way department_match reads it
against the department table: the clinic's data decides, not a word list in the agent. It finds a doctor said with or
without the word "doctor" ("Ronit Mukherjee dekhabo"), said with a case ending ("দাসের", "ঘোষবাবু"), or garbled a
little ("মুখার্জি" for "মুখার্জী"), and it finds EVERY doctor said -- "dr ghosh na ki jeno dr guha" is two doctors, and
the caller is asked which, never answered about one of them.

THE RULES, AND WHY EACH IS SAFE
-------------------------------
1. A doctor is mentioned by surname, by first name + surname, or by first name right beside "doctor".
2. A surname that is ALSO AN EVERYDAY WORD counts only beside a doctor title or that doctor's own first name. Measured
   against this clinic's 32 doctors: "কর"/"कर" (Dr. Kar) is the verb "do" -- "বুক কর", "book kar do" -- and "দে" (Dr. Dey)
   is "give". Without this rule, a caller asking to book anything would be offered Dr. Kar.
3. Short forms (three letters or fewer: দাস, সেন, পাল, ঘোষ, kar, roy) must match EXACTLY after spelling normalisation.
   Near-matching is only for forms of four or more letters, at a strict similarity. "বসেন" contains "সেন" and is never
   Dr. Sen: whole tokens only, never a substring.
4. A near match is reported as near (`exact: false`). The agent ASKS about it ("আপনি কি ... -এর কথা বলছেন?") and never
   books or reads a schedule on it -- CLAUDE.md's "Doctor Nobody" rule, kept.
5. A full name outranks a surname: "অরিন্দম সেন" is Dr. A. Sen alone, even though other doctors could share a surname.
   An exact match outranks a near one.
6. A surname said after what may be a DIFFERENT first name ("Ronit Mukherjee"; the clinic's Mukherjee is Sourav) is
   found but reported as near, so the caller is asked rather than answered about someone they did not name.

What this module does NOT do: decide which doctor the caller wants when several fit. It returns all of them; choosing
is the caller's job.
"""

from __future__ import annotations

import dataclasses
import difflib
import re
import unicodedata

# A near match needs this much similarity, on forms of at least NEAR_MIN_CHARS letters. REASONED from the spellings ASR
# and callers actually produce for this clinic's names ("মুখার্জি"/"মুখার্জী" = 1.00 once normalised, "mukharji"/
# "mukherjee" = 0.86, "chowdhuri"/"chowdhury" = 0.89), and well above unrelated pairs of the same length
# ("bagchi"/"banerjee" 0.43, "sinha"/"saha" 0.67). Recalibrate on real call transcripts.
NEAR_RATIO = 0.84
NEAR_MIN_CHARS = 4

# Rule 6's tolerance for a first name heard beside an exact surname. REASONED: Hindi ASR's "देवाशीज" for "देबाशीष"
# scores 0.71; a genuinely different first name scores far lower (measured: "ronit"/"sourav" 0.20, "রনিত"/"সৌরভ"
# 0.25, "অমিত"/"দেবাশিস" 0.18).
FIRST_NAME_BESIDE_SURNAME_RATIO = 0.65

# Doctor titles in every script and spelling a caller or ASR uses here. Normalised (see _norm) before comparison.
TITLE_WORDS = frozenset(
    {
        "dr",
        "doctor",
        "doc",
        "daktar",
        "dakter",
        "doktor",
        "dactor",
        "ডাক্তার",
        "ডাক্তারবাবু",
        "ডক্টর",
        "ডাঃ",
        "ডা",
        "ডঃ",
        "डॉक्टर",
        "डाक्टर",
        "डॉ",
        "डा",
        "डॉक्टरसाहब",
    }
)

# Surnames of this clinic's doctors that are also everyday words (rule 2). Every entry is a real collision, checked
# against the seeded doctors: kar/কর/कर "do", dey/de/দে/डे "give", ray/रे an English noun and a Hindi interjection,
# রায়/राय "verdict"/"opinion", সরকার "government", চাঁদা "donation", বিশ্বাস/विश्वास "trust" (Dr. Biswas), মিত্র/मित्र
# "friend" (Dr. Mitra), pal/পাল/पाल "friend"/"sail"/"turn" (Dr. Pal). Such a doctor is still found the way callers
# actually ask for them -- "ডাক্তার পাল", "Dr Kar", "অমিত কর" -- just not by the bare word.
EVERYDAY_WORDS = frozenset(
    {
        "kar",
        "dey",
        "de",
        "ray",
        "pal",
        "mitra",
        "কর",
        "দে",
        "রায়",
        "সরকার",
        "চাঁদা",
        "বিশ্বাস",
        "মিত্র",
        "পাল",
        "कर",
        "डे",
        "रे",
        "राय",
        "विश्वास",
        "बिस्वास",
        "मित्र",
        "मित्रा",
        "पाल",
    }
)

# Case endings and honorifics glued to a name in speech ("দাসের", "ঘোষবাবু", "senjee"). Tried longest first; the token
# is always also tried as said, so a surname that happens to end in one of these ("সরকার" ends in "র") is not cut.
_SUFFIXES = (
    "বাবুর",
    "বাবুকে",
    "বাবু",
    "দের",
    "েরা",
    "ের",
    "এর",
    "কে",
    "রা",
    "র",
    "দা",
    "দি",
    "जी",
    "का",
    "की",
    "के",
    "babu",
    "babur",
    "jee",
    "ji",
    "er",
    "ke",
)

_SPLIT = re.compile(r"[\s,.!?।॥;:'\"()\-–—/]+")

# Rule 6. A surname found right after a word that could be a DIFFERENT first name ("Ronit Mukherjee" when the clinic's
# Mukherjee is Sourav) is reported as near, so the caller is asked "do you mean Dr. Sourav Mukherjee?" instead of being
# answered about a doctor they did not name. These are the words that commonly stand before a name in a request and
# are NOT a first name, so they do not trigger that question. Missing a word here costs one extra "do you mean ...?",
# never a wrong doctor.
FUNCTION_WORDS = frozenset(
    {
        # Bengali
        "আমি",
        "আমার",
        "আমাকে",
        "আমরা",
        "একজন",
        "কোন",
        "কোনো",
        "ওই",
        "সেই",
        "এই",
        "আর",
        "বা",
        "না",
        "নাকি",
        "কি",
        "কী",
        "তো",
        "তাহলে",
        "শুধু",
        "ভালো",
        "ভাল",
        "মানে",
        "হ্যাঁ",
        "হাঁ",
        "আচ্ছা",
        "ঠিক",
        "আছে",
        "আপনাদের",
        "ওখানে",
        "এখানে",
        "দেখাব",
        "দেখাবো",
        "দেখাতে",
        "চাই",
        "কাল",
        "আজ",
        "পরশু",
        "সকালে",
        "বিকেলে",
        "উনি",
        "ওনার",
        "জন্য",
        "কাছে",
        # Hindi
        "मैं",
        "मुझे",
        "मेरा",
        "मेरे",
        "एक",
        "कोई",
        "उस",
        "इस",
        "और",
        "या",
        "नहीं",
        "कि",
        "तो",
        "हाँ",
        "हां",
        "अच्छा",
        "ठीक",
        "है",
        "आपके",
        "वहाँ",
        "यहाँ",
        "कल",
        "आज",
        "दिखाना",
        "चाहिए",
        "के",
        "से",
        "को",
        "का",
        "की",
        # English and romanised Bengali/Hindi
        "i",
        "me",
        "my",
        "a",
        "an",
        "the",
        "to",
        "see",
        "want",
        "with",
        "and",
        "or",
        "please",
        "yes",
        "no",
        "ok",
        "okay",
        "na",
        "ki",
        "ke",
        "ko",
        "ka",
        "aur",
        "ya",
        "mujhe",
        "ami",
        "amar",
        "ekjon",
        "kono",
        "oi",
        "sei",
        "ei",
        "ar",
        "ba",
        "naki",
        "toh",
        "haan",
        "ha",
        "accha",
        "thik",
        "kal",
        "aaj",
        "dekhabo",
        "dekhate",
        "chai",
        "show",
        "book",
        "visit",
        "consult",
        "meet",
        "for",
        "appointment",
    }
)

# Spelling variation that changes no name: vowel length, the three sibilants, retroflex n, nukta, candrabindu.
_FOLD = str.maketrans(
    {
        "ী": "ি",
        "ূ": "ু",
        "ঈ": "ই",
        "ঊ": "উ",
        "ষ": "স",
        "শ": "স",
        "ণ": "ন",
        "ঁ": None,
        "়": None,
        "ी": "ि",
        "ू": "ु",
        "ई": "इ",
        "ऊ": "उ",
        "ष": "स",
        "श": "स",
        "ण": "न",
        "ँ": None,
        "़": None,
    }
)
_LATIN_RULES = (
    (re.compile(r"(ee|ea|ie|iy)"), "i"),
    (re.compile(r"(oo|ou)"), "u"),
    (re.compile(r"sh"), "s"),
    (re.compile(r"w"), "v"),
    (re.compile(r"([bcdfgjklmnpqrstvxz])h"), r"\1"),  # aspirates: bh, dh, gh, kh, th
    (re.compile(r"y$"), "i"),
    (re.compile(r"(.)\1+"), r"\1"),  # doubled letters
)


def _norm(word: str) -> str:
    w = unicodedata.normalize("NFC", word or "").lower().strip(".,")
    w = unicodedata.normalize("NFC", w.translate(_FOLD))
    if w.isascii():
        for pattern, repl in _LATIN_RULES:
            w = pattern.sub(repl, w)
    return w


def tokens(text: str) -> list[str]:
    """The words of `text`, as said (NFC, lower-case), punctuation dropped."""
    return [t for t in _SPLIT.split(unicodedata.normalize("NFC", text or "").lower()) if t]


def _variants(token: str) -> list[str]:
    """The token as said, then with one case ending or honorific removed (rule: as-said first)."""
    out = [_norm(token)]
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix) + 1:
            out.append(_norm(token[: -len(suffix)]))
    return list(dict.fromkeys(v for v in out if v))


def _is_title(token: str) -> bool:
    return any(v in _TITLE_NORM for v in _variants(token))


_TITLE_NORM = frozenset(_norm(t) for t in TITLE_WORDS)
_EVERYDAY_NORM = frozenset(_norm(t) for t in EVERYDAY_WORDS)
_FUNCTION_NORM = frozenset(_norm(t) for t in FUNCTION_WORDS)


def _other_first_name_before(said: list[str], i: int, doctor: DoctorForms) -> bool:
    """Rule 6: is the word before position `i` plausibly a first name that is NOT this doctor's?"""
    if i == 0:
        return False
    prev = said[i - 1]
    if _is_title(prev) or any(_match(prev, f) for f in doctor.first_names):
        return False
    # Right beside an EXACT surname, a first name heard slightly wrong is still this doctor's ("देवाशीज" for "देबाशीष",
    # 0.71) -- a much looser bar than a first name must clear on its own, because the surname already identifies him.
    if any(
        difflib.SequenceMatcher(None, _norm(prev), _norm(f)).ratio() >= FIRST_NAME_BESIDE_SURNAME_RATIO
        for f in doctor.first_names
    ):
        return False
    if any(v in _FUNCTION_NORM for v in _variants(prev)) or any(ch.isdigit() for ch in prev):
        return False
    # Letters and combining marks: Bengali and Devanagari vowel signs (ি, ী, ा) are marks, and str.isalpha() is False
    # for them, which made every Bengali first name look like "not a word".
    return len(prev) >= 3 and all(unicodedata.category(c)[0] in "LM" for c in prev)


def _match(token: str, form: str) -> str | None:
    """'exact', 'near' or None: does one spoken token stand for one written form?"""
    f = _norm(form)
    if not f:
        return None
    best = None
    for v in _variants(token):
        if v == f:
            return "exact"
        if len(f) >= NEAR_MIN_CHARS and len(v) >= NEAR_MIN_CHARS:
            if difflib.SequenceMatcher(None, v, f).ratio() >= NEAR_RATIO:
                best = "near"
    return best


@dataclasses.dataclass(frozen=True)
class DoctorForms:
    """Everything a caller might call one doctor. `key` is the canonical name ("Dr. D. Das")."""

    key: str
    first_names: tuple[str, ...]  # "debasish", "দেবাশিস", "देबाशीष"
    surnames: tuple[str, ...]  # "das", "দাস", "दास" and every alias


@dataclasses.dataclass(frozen=True)
class Mention:
    key: str
    strength: str  # "full" | "titled" | "surname" | "first"
    exact: bool


def _find(said: list[str], forms: tuple[str, ...]) -> list[tuple[int, str]]:
    """(position, 'exact'|'near') for every spoken token that stands for one of `forms`."""
    hits = []
    for i, token in enumerate(said):
        kinds = [k for k in (_match(token, f) for f in forms) if k]
        if kinds:
            hits.append((i, "exact" if "exact" in kinds else "near"))
    return hits


def _beside(said: list[str], i: int, test) -> bool:
    return any(0 <= j < len(said) and test(said[j]) for j in (i - 1, i + 1))


def mention_of(said: list[str], doctor: DoctorForms) -> Mention | None:
    """How (if at all) the spoken tokens mention this one doctor."""
    surname_hits = _find(said, doctor.surnames)
    first_hits = _find(said, doctor.first_names)
    first_at = {i for i, _ in first_hits}

    if surname_hits and first_hits:
        exact = any(k == "exact" for _, k in surname_hits) and any(k == "exact" for _, k in first_hits)
        return Mention(doctor.key, "full", exact)

    usable = []
    for i, kind in surname_hits:
        everyday = _norm(said[i]) in _EVERYDAY_NORM or any(v in _EVERYDAY_NORM for v in _variants(said[i]))
        if _other_first_name_before(said, i, doctor):
            kind = "near"  # rule 6: found, but asked about -- the caller may have named someone else
        # "ডাক্তার পলাশ মিত্র": a title, a first name, then the surname -- the way a doctor's whole name is said. It counts
        # as said-with-"doctor" even when the surname is an everyday word; rule 6 has already marked it near when that
        # first name is not this doctor's, so the caller is ASKED about Dr. Rina Mitra, never told her hours (live
        # 2026-10-07: "ডাক্তার পলাশ মিত্র কবে বসেন?" was answered with Dr. Rina Mitra's schedule).
        titled_whole_name = i >= 2 and _is_title(said[i - 2]) and not _is_title(said[i - 1])
        if _beside(said, i, _is_title) or i - 1 in first_at or titled_whole_name:
            usable.append(("titled", kind))
        elif not everyday:
            usable.append(("surname", kind))
    if usable:
        strength = "titled" if any(s == "titled" for s, _ in usable) else "surname"
        return Mention(doctor.key, strength, any(k == "exact" for _, k in usable))

    for i, kind in first_hits:
        if _beside(said, i, _is_title):
            return Mention(doctor.key, "first", kind == "exact")
    return None


def mentioned(text: str, doctors: list[DoctorForms]) -> list[Mention]:
    """Every doctor `text` mentions, most specific first (rule 5), ties in the order given."""
    said = [t for t in tokens(text) if t]
    found = [m for m in (mention_of(said, d) for d in doctors) if m]
    # Rule 5, by strength: a full name, then a name said with "doctor", then a first name beside "doctor", then a bare
    # surname. The caller who says "ডাক্তার দাস" and, in the same breath, the PATIENT's name "রাহুল বসু" means Dr. Das:
    # without this, "বসু" (Dr. Basu, Dr. Bose) made three doctors of one -- story "Caller gives everything in one
    # sentence", where a patient name is always present.
    for strength in ("full", "titled", "first", "surname"):
        if any(m.strength == strength for m in found):
            found = [m for m in found if m.strength == strength]
            break
    if any(m.exact for m in found):
        found = [m for m in found if m.exact]
    return found
