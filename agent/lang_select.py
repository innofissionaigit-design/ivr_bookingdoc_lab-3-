"""Choosing the language of a turn when language ID alone is not enough.

Measured on the pod (the numbers are recorded in the constants below and in
docs/pod-verification-checklist.md; docs/adr/0002 covers the turn-by-turn reply
language, early language ID and the ambiguity hand-off built on them): SpeechBrain
VoxLingua107 separates Bengali from Hindi and Hindi from Bengali well, but
Indian-accented ENGLISH is routinely labelled Hindi, at high confidence. A
confident-looking wrong answer is worse than an unsure one: the router
commits to it and the caller is answered in the wrong language, so the
decision cannot rest on LID's top label alone.

The pieces here are deliberately small and pure so they are testable
without a GPU:

  script_share   what fraction of a transcript is in a language's own script
  pick_candidate choose between two ASR results for the same audio
  speakable      can a language's TTS voice actually say this text
"""

from __future__ import annotations

from typing import Protocol

from agent.lid import DEFAULT_CONFIDENCE_FLOOR, SUPPORTED_LANGUAGES, LIDResult
from agent.speech_norm import unspeakable_spans
from agent.spoken_digits import only_a_number

_SCRIPT_RANGE = {"bn": ("ঀ", "৿"), "hi": ("ऀ", "ॿ")}
_WRONG_SCRIPT = {"bn": ("ऀ", "ॿ"), "hi": ("ঀ", "৿")}
_SHARED_PUNCTUATION = frozenset(chr(0x0964) + chr(0x0965))


def script_share(text: str, lang: str) -> float:
    """Fraction of the letters in `text` written in `lang`'s own script."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    if lang == "en":
        return sum(c.isascii() for c in letters) / len(letters)
    lo, hi = _SCRIPT_RANGE[lang]
    return sum(lo <= c <= hi for c in letters) / len(letters)


def speakable(text: str, lang: str) -> bool:
    """Can `lang`'s voice actually say `text`? Model-written smalltalk is the
    one place reply text is not from a template, so it is checked: a reply in
    the wrong script is dropped by the tokenizer and heard as silence."""
    if not text or unspeakable_spans(text, lang):
        return False
    wrong = _WRONG_SCRIPT.get(lang)
    # The danda (U+0964) and double danda (U+0965) sit in the Devanagari block but are the full stop of Bengali
    # too. Counting them as "wrong script" made every Bengali reply ending in "।" unspeakable, so it was
    # silently replaced by a default line.
    return not (wrong and any(wrong[0] <= c <= wrong[1] and c not in _SHARED_PUNCTUATION for c in text))


# ---------------------------------------------------------------- selection

# A second language must carry at least this much LID probability before the
# top label is treated as unsure. Measured on the pod: Indian-accented
# English is labelled Hindi at 0.90-0.91 with English at 0.09-0.36, while
# genuine Hindi and Bengali put 0.00 on English. So "any real mass on a
# second language" is the signal, not the top label's own confidence.
SECOND_LANGUAGE_FLOOR = 0.03


def languages_to_verify(language: str, scores: dict[str, float], active: tuple[str, ...]) -> list[str] | None:
    """Every active language, best LID score first, when LID is not decisive;
    None when it is.

    Not decisive means: LID found none of our languages ("unknown"), or a
    second language carries real probability. Verifying with ALL active ASRs
    rather than the top two is deliberate: measured, English speech came back
    as bn 0.83 / hi 0.16 / en 0.01, so English was not even in the top two,
    and only running its ASR could have found it."""
    ranked = [lang for lang, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True) if lang in active]
    for lang in active:
        if lang not in ranked:
            ranked.append(lang)
    if language == "unknown":
        return ranked
    second = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0.0
    return ranked if second >= SECOND_LANGUAGE_FLOOR else None


# A second Indic language is only worth running a recogniser for when language ID is genuinely torn between the two.
# Below this it cannot win anyway (pick_candidate: an Indic engine other than language ID's favourite needs SWITCH_MIN_LID
# to overrule it, and needs INDIC_MIN_LID just to be considered), so its ~0.6 s of GPU time buys nothing.
OTHER_INDIC_MIN_LID = 0.30


def engines_needed(
    language: str, scores: dict[str, float], active: tuple[str, ...], prior: str | None = None
) -> list[str]:
    """The recognisers that can still change the answer, best language-ID score first (ties keep that order, so the
    LID-preferred language wins them). Measured on the pod: running all three took ~0.64 s; English took 0.21 s.

      * unknown language, or no scores: all of them, as before;
      * otherwise language ID's favourite, plus the OTHER Indic language only if LID is torn (>= OTHER_INDIC_MIN_LID);
      * English ALWAYS: it is cheap and concurrent, and language ID mislabels accented English as Bengali or Hindi
        (measured: English speech came back as bn 0.83 / hi 0.16 / en 0.01), so its probability cannot rule it out;
      * the call's own language (`prior`) ALWAYS. pick_candidate can only refuse a switch on the say-so of one recogniser
        if it has the call language's own transcript to stay with. Live call 2026-10-07: a Bengali caller said a doctor's
        name, language ID gave hi 0.80 / bn 0.20, Bengali fell under OTHER_INDIC_MIN_LID and was not run, so there was
        nothing to stay with and the call went to Hindi. The orchestrator starts this recogniser before language ID
        returns anyway (main_pcm._route_and_transcribe), so including it costs nothing; leaving it out threw it away."""
    ranked = [lang for lang, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True) if lang in active]
    for lang in active:
        if lang not in ranked:
            ranked.append(lang)
    if language == "unknown" or not scores:
        return ranked
    need = []
    for lang in ranked:
        if lang == "en" or lang == language or scores.get(lang, 0.0) >= OTHER_INDIC_MIN_LID:
            need.append(lang)
    if language in active and language not in need:
        need.insert(0, language)
    if prior in active and prior not in need:
        need.append(prior)
    return need


# English gate. Measured on synthetic clips (clean, 8 kHz, and through the live
# service), decoder agreement of the ENGLISH engine: 0.67-1.00 on English
# audio (n=7 runs), 0.00-0.43 on Bengali and Hindi audio (n=8). The Bengali engine cannot be used the same way: it
# transliterates English with agreement up to 1.00, so on English audio "the
# engine that agrees with itself most" picks Bengali. REASONED from a small
# synthetic sample, not calibrated on telephony -- recalibrate on real calls.
ENGLISH_MIN_AGREEMENT = 0.6


# An Indic (Bengali/Hindi) recogniser may only WIN on its own confidence if language ID gave that language at
# least this much probability. Found on the live pod: LID said Bengali 0.95 / Hindi 0.05, all three recognisers ran,
# and the Hindi one (which writes Bengali speech out in Devanagari, with a confident-looking 0.50) beat the Bengali
# one (0.20), so the caller was answered in Hindi. Recogniser confidences of different models are not comparable;
# language ID is the one signal that is. English is exempt: LID mislabels accented English, which is exactly why
# the English engine has its own decoder-agreement gate.
INDIC_MIN_LID = 0.10

# English may win outright on its own decoder agreement (LID mislabels accented English), but that gate alone let a
# Bengali sentence through: the English recogniser wrote it out in Latin letters with agreement 1.00 while language ID
# had given English 0.00, and the call was answered in English. So English must ALSO be real English: language ID
# gives it some probability, or most of what it wrote is words an English speaker actually says.
ENGLISH_MIN_LID = 0.05
ENGLISH_MIN_WORD_SHARE = 0.5
_ENGLISH_WORDS = frozenset(
    """
a an the and or but if so of to in on at for from with by about as is are was were be been am do does did have has had
i me my we our you your he she it they them this that these those there here what which who whom when where why how
can could will would shall should may might must not no yes ok okay please thanks thank hello hi sorry
price prices cost costs rate rates fee charge how much many test tests doctor doctors dr appointment appointments book
booking cancel reschedule time timing timings open close hours today tomorrow morning evening day week monday tuesday
wednesday thursday friday saturday sunday fasting fast empty stomach report reports result results sample blood urine
clinic address location parking insurance payment card cash number phone confirm confirmation need want tell give
know check see get take come go available sit sits sitting chamber counter staff person help
""".split()
)


def english_word_share(text: str) -> float:
    """Share of the tokens that are ordinary English words (a small fixed list). A Bengali or Hindi sentence written
    out in Latin letters ("sibisi test rate koto") scores low; a real English question scores high."""
    tokens = [t.strip(".,?!'\"").lower() for t in (text or "").split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return 0.0
    return sum(t in _ENGLISH_WORDS for t in tokens) / len(tokens)


# Leaving the language the call has been in needs language ID to be DECISIVE about the new one -- the same evidence
# that lets it commit to a language on its own (no second language carrying SECOND_LANGUAGE_FLOOR), not merely a lean.
#
# It was 0.50 ("believes it"), and a live call on 2026-10-07 showed what that costs. A Bengali caller answered "which
# doctor?" with only a name, "ডক্টর দেবাশিস দাস". A name sounds the same in either language, and language ID scored it
# hi 0.80 / bn 0.20 -- logged by this very system as "not decisive" -- and the call switched to Hindi on it, disclosure
# notice and all. Every genuine-language turn on that pod scored 1.00. So 0.80 is not evidence of a new language; it is
# what an utterance with no language in it looks like. A caller who really changes language is still followed: a
# decisive turn moves the call (the router commits it before this is ever consulted), and so does an explicit request.
SWITCH_MIN_LID = 1.0 - SECOND_LANGUAGE_FLOOR

# A recogniser whose two decoders agree at least this much is convincing. Same measurement as ENGLISH_MIN_AGREEMENT:
# a decoder pair trained on the speech's own language agrees 0.67-1.00, the wrong one 0.00-0.67. REASONED from a small
# synthetic sample, not calibrated on telephony.
CONVINCING_MIN_AGREEMENT = 0.6


def lid_is_unsure(lid: LIDResult, active: tuple[str, ...], floor: float = DEFAULT_CONFIDENCE_FLOOR) -> bool:
    """Language ID did not settle this turn on its own: no language of ours, below the confidence floor, or a
    second language carrying real probability (languages_to_verify)."""
    if lid.language not in SUPPORTED_LANGUAGES or lid.confidence < floor:
        return True
    return bool(lid.scores) and languages_to_verify(lid.language, lid.scores, active) is not None


def outcome_is_ambiguous(lid: LIDResult, result: _Recognition, active: tuple[str, ...]) -> bool:
    """The turn left the call guessing at its language: language ID was unsure AND the recogniser that won is not
    convincing either (no text, or its decoders disagree). One without the other is not ambiguity -- a short "yes"
    gets a low language-ID score yet reads cleanly, and a confident label over a garbled transcript is a
    transcript problem (agent/transcript_rules), not a language one. Feeds ASRLanguageRouter.note_turn_outcome."""
    unconvincing = not (result.text or "").strip() or result.decoder_agreement < CONVINCING_MIN_AGREEMENT
    return unconvincing and lid_is_unsure(lid, active)


class _Recognition(Protocol):
    """What the picker reads of one recogniser's result (agent/asr.ASRResult has both)."""

    text: str
    decoder_agreement: float


# ---- what the transcripts themselves say about the language ---------------------------------------------------------
#
# Language ID is not enough to leave the call's language between Bengali and Hindi, even when it is "decisive". Live,
# 2026-10-07, synthetic Bengali callers (this stack's own Bengali voice):
#     "ভিটামিন সি টেস্টের দাম কত?"           LID hi 0.97 -> committed Hindi
#     "আমি একটা সিবিসি টেস্ট বুক করতে চাই"   LID hi 0.99 on the first words
# Loanwords (vitamin, CBC, test, book, doctor, appointment) carry most of these sentences and sound the same in both
# languages. The GRAMMAR does not: the Bengali recogniser wrote "টেস্টের দাম কত" (the -এর ending, "কত"), the Hindi one
# wrote "टेस्टिड दांग" -- no है, no का, no कितना. A recogniser trained on a language writes that language's function
# words when it hears them, and cannot invent them from the other language's speech.
#
# So leaving the call's language for the other Indic one also needs the new language's transcript to carry MORE of its
# own grammar words than the call language's transcript carries of its own. A bare name or a lone loanword has none in
# either, and the call stays where it is -- the case this was built for. A genuine Hindi sentence ("सीबीसी टेस्ट का रेट
# कितना है") carries several, and its Bengali transcript none, so the call follows the caller.
_GRAMMAR_WORDS = {
    "bn": frozenset(
        """আমি আমার আমাকে আমরা আমাদের আপনি আপনার আপনাকে আপনাদের তুমি তোমার ওনার উনি তিনি এটা ওটা সেটা একটা
        ওই সেই কত কেন কোথায় কবে কখন কোনো কারা কিভাবে কীভাবে করতে করব করবো করছি করেছি করুন করা
        চাই চাইছি হবে হয়েছে আছে আছেন নেই হ্যাঁ বলুন বলবেন বলছি বলছেন যাবে যাব পাব পারব পারি দেবেন দেখাব
        দেখাবো দেখাতে লাগবে দরকার এবং তাহলে থেকে জন্য সাথে সঙ্গে কাছে মধ্যে কিন্তু ভালো""".split()
    ),
    "hi": frozenset(
        """है हैं था थी थे हूँ हूं का में पर ने मैं मुझे मेरा मेरी मेरे हम हमें हमारा आप आपका आपकी आपके
        आपको तुम वह वे यह उसका उनका उनकी क्या कितना कितने कितनी कहाँ कहां क्यों कैसे कौन कौनसा कौन-सा
        चाहिए चाहता चाहती करना करनी करने करो कीजिए दीजिए बताइए बताओ बताएँ दिखाना दिखाने मिलेगा मिलेगी होगा होगी
        नहीं हाँ हां और भी लेकिन लिए साथ पास वाला वाली""".split()
    ),
}
# Deliberately LEFT OUT of both lists: short words that sound alike across the two languages, because a recogniser can
# write the other language's speech as its own word -- Hindi के/से/की/तो/ना/कि heard by the Bengali recogniser come out
# as কে/সে/কি/তো/না, all Bengali words; Bengali কি/কে/তো/সে heard by the Hindi one come out as की/के/तो/से. Also দিন
# (= दिन, "day") and করে/करे. A word either recogniser could write for EITHER language's speech proves nothing.
_BENGALI_ENDINGS = ("ের", "টা", "টি", "গুলো", "দের", "য়ের")  # "টেস্টের", "সিবিসিটা": grammar glued to a loanword


def grammar_words(text: str, lang: str) -> int:
    """How many of `lang`'s own grammar words (and, for Bengali, case endings on a word) `text` contains."""
    words = [w.strip(".,?!।॥'\"") for w in (text or "").split()]
    known = _GRAMMAR_WORDS.get(lang, frozenset())
    n = sum(w in known for w in words if w)
    if lang == "bn":
        n += sum(1 for w in words if w and w not in known and len(w) > 3 and w.endswith(_BENGALI_ENDINGS))
    return n


def transcript_supports_switch(new_lang: str, new_text: str, stay_lang: str, stay_text: str) -> bool:
    """Do the transcripts back leaving `stay_lang` for `new_lang`? Only judged between Bengali and Hindi (English has its
    own gate in pick_candidate); any other pair is left to language ID, as before."""
    if new_lang == stay_lang or new_lang not in _GRAMMAR_WORDS or stay_lang not in _GRAMMAR_WORDS:
        return True
    return grammar_words(new_text, new_lang) > grammar_words(stay_text, stay_lang)


def pick_candidate(
    candidates: list[tuple[str, _Recognition]], lid_scores: dict[str, float] | None = None, prior: str | None = None
):
    """Several ASR engines ran on the same audio. Return (language, result)
    for the one that most plausibly heard its own language.

    1. English wins outright if the English engine's CTC and RNNT decoders
       agree (>= ENGLISH_MIN_AGREEMENT) and it produced Latin text -- the
       one engine whose agreement is a trustworthy "this was English" signal.
    2. Otherwise choose among the rest by transcript-in-own-script, then by
       decoder agreement (own-language 0.67-1.00 vs 0.00-0.67 for the wrong
       Indic engine, measured).
    Ties go to the earlier candidate, i.e. the LID-preferred one."""
    usable = [(lang, r) for lang, r in candidates if getattr(r, "text", "").strip()]
    if not usable:
        return candidates[0]
    # A number read out in English digit names ("zero one two ...") by a Bengali or Hindi caller is still their call:
    # live 2026-10-07, "giro won two three ..." mid-cancellation switched the call to English, disclosure and all.
    number_in_english = prior not in (None, "en") and any(lang == prior for lang, _ in usable)
    for lang, r in usable:
        if lang == "en" and number_in_english and only_a_number(r.text):
            continue
        if lang == "en" and r.decoder_agreement >= ENGLISH_MIN_AGREEMENT and script_share(r.text, "en") >= 0.9:
            # With language ID scores in hand, "English" must also be English (see ENGLISH_MIN_WORD_SHARE).
            if (
                lid_scores is None
                or lid_scores.get("en", 0.0) >= ENGLISH_MIN_LID
                or english_word_share(r.text) >= ENGLISH_MIN_WORD_SHARE
            ):
                return lang, r
    rest = [(lang, r) for lang, r in usable if lang != "en"] or usable
    # The call language's own transcript, kept aside BEFORE the filter below. That filter stops an engine language ID does
    # not believe from WINNING; it must not also stop the call from STAYING. Live: LID hi 0.97 / bn 0.03 on Bengali
    # loanwords dropped the Bengali transcript here, and with it the only evidence that the caller never left Bengali.
    staying = [(lang, r) for lang, r in rest if lang == prior] if prior else []
    if lid_scores:
        believed = [(lang, r) for lang, r in rest if lid_scores.get(lang, 0.0) >= INDIC_MIN_LID]
        rest = believed or rest
    best = max(rest, key=lambda t: (round(script_share(t[1].text, t[0]), 1), t[1].decoder_agreement))
    if lid_scores and not prior:
        # First turn: no call language yet, so language ID's own favourite among these is the one to stay with.
        prior = max((lang for lang, _ in rest), key=lambda lang: lid_scores.get(lang, 0.0))
    if lid_scores and prior and best[0] != prior:
        stay = [(lang, r) for lang, r in rest if lang == prior] or staying
        if stay and (
            lid_scores.get(best[0], 0.0) < SWITCH_MIN_LID  # no switch on a recogniser's say-so alone
            or not transcript_supports_switch(best[0], best[1].text, prior, stay[0][1].text)  # nor on loanwords
        ):
            return stay[0]
    return best
