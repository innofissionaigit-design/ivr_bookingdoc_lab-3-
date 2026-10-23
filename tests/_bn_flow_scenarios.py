"""The three Bengali scenarios, one function each, each playing ONE whole call and returning a Result.
See tests/_bn_flow_matrix.py for what is real and what is given; this file is the scripts."""

from __future__ import annotations

import datetime as dt
import re
import time

from _bn_flow_matrix import (  # noqa: E402
    B,
    PATIENTS,
    Result,
    _WRONG_LINES,
    _repeats,
    _spoken,
    date_phrase,
    next_dates,
    phone_phrase,
    time_phrase,
)

_ORDINALS_BN = ["প্রথমজন", "দ্বিতীয়জন", "তৃতীয়জন", "চতুর্থজন"]
DATE_STYLES = ("relative", "weekday_number", "number_month")
TIME_STYLES = ("fraction", "minutes", "digits")
PHONE_STYLES = ("bn_digits", "latin", "words")
PICKS = ("full_name", "surname", "ordinal")
DOCTOR_ONLY_STYLES = ("full_name", "title_full_name", "wants_to_see", "surname", "title_surname")
ORDERS = ("step_by_step", "all_in_one")

_VOWEL_SIGNS = "ািীুূেৈোৌ"


def _dept_stem(name_bn: str) -> str:
    return name_bn.split(" ও ")[0].strip()


def _locative(stem: str) -> str:
    return stem + ("তে" if stem[-1] in _VOWEL_SIGNS else "ে")


def department_openers(name_bn: str, aliases: list[str]) -> list[tuple[str, str, str, dict]]:
    """How a caller opens with a department: (style, what they say, intent, slots the model gave)."""
    stem = _dept_stem(name_bn)
    alias = next((a for a in aliases if "ডাক্তার" in a and re.search("[ঀ-৿]", a)), None)
    out = [
        ("locative", f"আমি {_locative(stem)} একজন ডক্টরকে দেখাব", B, {}),
        ("genitive_question", f"{stem}র ডাক্তার দেখাতে চাই", "department_query", {"symptom_description": stem}),
    ]
    if alias:
        out.append(("kind_of_doctor", f"আমার {alias} দরকার", B, {"doctor_name": alias}))
    return out


def _finish(call, res: Result, started: float) -> Result:
    spoken = _spoken(call)
    for line in spoken:
        for bad in _WRONG_LINES:
            if bad in line:
                res.fail(f"wrong line spoken: {line}")
    for rep in _repeats(spoken):
        res.fail(f"the same line was spoken twice in a row: {rep}")
    if call.blocked:
        res.fail(f"lines the voice cannot say: {call.blocked}")
    res.transcript = call.transcript()
    res.seconds = time.time() - started
    return res


def allocate(w, doctor: dict, today: dt.date) -> tuple[dt.date, str]:
    """A (day, start time) nobody has booked yet, inside the doctor's chamber and within the next seven days (so a
    weekday name, or "tomorrow", still means exactly that day)."""
    dates = next_dates(doctor["chamber_days"], 3, today)
    slots = doctor["slots"]
    while True:
        k = w.fresh_index(doctor["name"])
        if k >= len(dates) * len(slots):
            raise RuntimeError(f"no free slot left for {doctor['name']}")
        day, slot = dates[k // len(slots)], slots[k % len(slots)]
        if (doctor["name"], day.isoformat(), slot) not in w.taken:  # the clinic's own seeded appointment
            return day, slot


def _confirm_and_verify(call, res: Result, *, doctor: dict, day: dt.date, slot: str, patient: str, phone: str,
                        readback_has: list[str]) -> None:
    """The read-back must carry every fact; "yes" must give a confirmation number; the clinic must hold exactly that
    booking, found by its number and by the caller's phone."""
    readback = next((ln for ln in reversed(_spoken(call)) if ln.endswith("?") and "কনফার্ম" in ln), "")
    if not readback:
        res.fail("no read-back question was asked before confirming")
    else:
        for needle in readback_has:
            if needle not in readback:
                res.fail(f"read-back lacks {needle!r}: {readback}")
    final = call.say("হ্যাঁ", "unclear", {})
    ids = re.findall(r"KCD-[0-9A-Z-]+", " ".join(final))
    if not ids:
        res.fail(f"no confirmation number after yes: {final}")
        return
    client = call.m._tools._client
    rows = call._run(client.get("/api/v1/bookings/lookup", params={"confirmation_id": ids[0]})).json().get("bookings") or []
    if len(rows) != 1:
        res.fail(f"clinic holds {len(rows)} bookings for {ids[0]}")
        return
    r = rows[0]
    for key, want in (("doctor_name", doctor["name"]), ("date", day.isoformat()), ("time_slot", slot),
                      ("patient_name", patient)):
        if r.get(key) != want:
            res.fail(f"stored {key} is {r.get(key)!r}, the caller meant {want!r}")
    by_phone = call._run(client.get("/api/v1/bookings/lookup", params={"phone": phone})).json()
    if ids[0] not in [b.get("confirmation_id") for b in by_phone.get("bookings") or []]:
        res.fail(f"the booking is not found by the phone number {phone}")


# ============================================================================== scenario 1
def department_to_booking(w, doctor: dict, dept_doctors: list[dict], opener: tuple, pick: str, date_style: str,
                          time_style: str, phone_style: str, today: dt.date) -> Result:
    started = time.time()
    combo = {"doctor": doctor["name"], "department": doctor["department"], "opener": opener[0], "pick": pick,
             "date_style": date_style, "time_style": time_style, "phone_style": phone_style}
    res = Result("1 department -> doctor -> day and time -> confirmed", combo)
    call = w.call()
    try:
        day, slot = allocate(w, doctor, today)
        patient = PATIENTS[w.fresh_index("patient") % len(PATIENTS)]
        phone = w.fresh_phone()
        _style, said, intent, slots = opener
        out = call.say(said, intent, dict(slots))
        listed = [d["full_name_bn"] for d in dept_doctors]
        if not any(all(n in ln for n in listed) for ln in out):
            res.fail(f"the department's doctors were not all read out: {out}")
        st = call.session.booking
        order = [c[0] for c in (st.doctor_choices if st else [])]
        if doctor["name"] not in order:
            res.fail(f"{doctor['name']} is not among the doctors offered: {order}; said {out}")
            return res
        if pick == "full_name":
            out = call.say(f"{doctor['full_name_bn']}কে দেখাতে চাই", B, {"doctor_name": doctor["full_name_bn"]})
        elif pick == "surname":
            out = call.say(doctor["alias_bn"], B, {"doctor_name": doctor["alias_bn"]})
        else:  # a position in the list that was read out
            out = call.say(_ORDINALS_BN[order.index(doctor["name"])], "unclear", {})
        st = call.session.booking
        if st is None or not st.doctor_verified or st.slots.get("doctor_name") != doctor["name"]:
            res.fail(f"the doctor was not settled as {doctor['name']}: {st and st.slots}; said {out}")
            return res
        call.say(date_phrase(day, today, date_style), B, {"date": day.isoformat()})
        st = call.session.booking
        if not st or st.slots.get("date") != day.isoformat():
            res.fail(f"the day was not taken as {day}: {st and st.slots.get('date')}")
        call.say(time_phrase(slot, time_style), B, {"time_slot": slot})
        st = call.session.booking
        if not st or st.slots.get("time_slot") != slot:
            res.fail(f"the time was not taken as {slot}: {st and st.slots.get('time_slot')}")
        call.say(f"রোগীর নাম {patient}", B, {"patient_name": patient})
        call.say(phone_phrase(phone, phone_style), B, {"phone": phone})
        _confirm_and_verify(call, res, doctor=doctor, day=day, slot=slot, patient=patient, phone=phone,
                            readback_has=[doctor["alias_bn"], slot, patient, phone])
    except Exception as exc:  # a crash is a failed scenario, not a lost report
        res.fail(f"exception: {type(exc).__name__}: {exc}")
    finally:
        _finish(call, res, started)
        w.close(call)
    return res


# ============================================================================== scenario 2
def doctor_only(w, doctor: dict, everyone: list[dict], name_style: str, order: str, date_style: str, time_style: str,
                phone_style: str, today: dt.date) -> Result:
    started = time.time()
    combo = {"doctor": doctor["name"], "department": doctor["department"], "name_style": name_style, "order": order,
             "date_style": date_style, "time_style": time_style, "phone_style": phone_style}
    res = Result("2 only a doctor's name -> the rest of the booking", combo)
    call = w.call()
    try:
        day, slot = allocate(w, doctor, today)
        patient = PATIENTS[w.fresh_index("patient") % len(PATIENTS)]
        phone = w.fresh_phone()
        full, alias = doctor["full_name_bn"], doctor["alias_bn"]
        said = {
            "full_name": full,
            "title_full_name": f"ডাক্তার {full}",
            "wants_to_see": f"{full}কে দেখাতে চাই",
            "surname": alias,
            "title_surname": f"ডাঃ {alias}",
        }[name_style]
        slot_name = alias if name_style in ("surname", "title_surname") else full
        shared_surname = sum(1 for d in everyone if d["alias_bn"] == alias) > 1
        extra = {}
        if order == "all_in_one":
            said = f"{said} {date_phrase(day, today, date_style)} {time_phrase(slot, time_style)}"
            extra = {"date": day.isoformat(), "time_slot": slot}
        out = call.say(said, B, {"doctor_name": slot_name, **extra})
        st = call.session.booking
        if shared_surname and name_style in ("surname", "title_surname"):
            # Two doctors answer to this surname: the clinic must ask which, never pick one.
            if st and st.doctor_verified and st.slots.get("doctor_name") != doctor["name"]:
                res.fail(f"{alias} fits more than one doctor but another was chosen: {st.slots.get('doctor_name')}")
                return res
            if not (st and st.doctor_verified):
                out = call.say(full, B, {"doctor_name": full})
                st = call.session.booking
        if st is None or not st.doctor_verified or st.slots.get("doctor_name") != doctor["name"]:
            res.fail(f"the doctor was not settled as {doctor['name']}: {st and st.slots}; said {out}")
            return res
        if order == "step_by_step":
            call.say(date_phrase(day, today, date_style), B, {"date": day.isoformat()})
            call.say(time_phrase(slot, time_style), B, {"time_slot": slot})
        st = call.session.booking
        if not st or st.slots.get("date") != day.isoformat() or st.slots.get("time_slot") != slot:
            res.fail(f"day/time not taken as {day} {slot}: {st and (st.slots.get('date'), st.slots.get('time_slot'))}")
        call.say(f"রোগীর নাম {patient}", B, {"patient_name": patient})
        call.say(phone_phrase(phone, phone_style), B, {"phone": phone})
        _confirm_and_verify(call, res, doctor=doctor, day=day, slot=slot, patient=patient, phone=phone,
                            readback_has=[alias, slot, patient, phone])
    except Exception as exc:
        res.fail(f"exception: {type(exc).__name__}: {exc}")
    finally:
        _finish(call, res, started)
        w.close(call)
    return res


# ============================================================================== scenario 3
def load_catalogue(call) -> None:
    from agent.fast_path import Catalogue, FastPath

    call.m._fast_path = FastPath(Catalogue(call._run(call.m._tools._client.get("/api/v1/catalogue")).json()))


def test_prices(w, tests: list[dict], phrasing: str, slot_style: str, intent: str) -> Result:
    """`tests`: 1..n entries of the clinic's test list (test_name, test_name_bn, rate_inr)."""
    from agent.reply_templates_i18n import _rupees

    started = time.time()
    combo = {"tests": [t["test_name"] for t in tests], "count": len(tests), "phrasing": phrasing,
             "slot_style": slot_style, "intent": intent}
    res = Result("3 the price of one or more tests", combo)
    call = w.call()
    try:
        load_catalogue(call)
        spoken_names = [t["test_name_bn"] for t in tests]
        if len(spoken_names) < 3:
            joined = " আর ".join(spoken_names)
        else:
            joined = ", ".join(spoken_names[:-1]) + " আর " + spoken_names[-1]
        said = {
            "price": f"{joined}-এর দাম কত",
            "how_much": f"{joined} করাতে কত লাগবে",
            "total": f"{joined} টেস্টের মোট দাম কত হবে",
        }[phrasing]
        names = [t["test_name"] for t in tests] if slot_style == "canonical" else list(spoken_names)
        slots = {"test_names": names} if len(names) > 1 else {"test_name": names[0]}
        first = call.say(said, intent, slots)
        asked = first[-1] if first else ""
        if asked.endswith("?"):
            reply = " ".join(call.say("হ্যাঁ", "unclear", {}))  # several tests are read back, and confirmed, first
        else:
            reply = " ".join(first)
        for t in tests:
            if _rupees(t["rate_inr"]) not in reply:
                res.fail(f"price of {t['test_name']} ({_rupees(t['rate_inr'])}) is not in the reply: {reply}")
        total = sum(t["rate_inr"] for t in tests)
        if len(tests) > 1 and _rupees(total) not in reply:
            res.fail(f"the total {_rupees(total)} is not in the reply: {reply}")
        if len(tests) > 1 and "মোট" not in reply:
            res.fail(f"the reply does not say it is a total: {reply}")
        if call.session.booking is not None:
            res.fail("a booking was opened for a price question")
    except Exception as exc:
        res.fail(f"exception: {type(exc).__name__}: {exc}")
    finally:
        _finish(call, res, started)
        w.close(call)
    return res
