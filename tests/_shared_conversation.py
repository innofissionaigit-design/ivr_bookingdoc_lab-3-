"""Many whole phone conversations against ONE seeded clinic, for matrix tests.

tests/_conversation.py gives every call its own freshly seeded clinic database (~3 s each), which is right for a
regression test and far too slow for "every doctor x every way of saying the day x every way of saying the time".
This keeps the same REAL orchestrator and REAL clinic API (the same ClinicToolsClient over an in-memory ASGI
transport) and the same scripted-intent / given-transcript line (see _conversation.py for why that is the right
line), but seeds once:

    with world("bn") as w:
        call = w.call()            # a new CallSession; the clinic's bookings persist between calls
        call.say("...", "book_appointment", {...})

Because the database is shared, a booking confirmed in one call is a taken slot in the next. A matrix must give each
call its own (doctor, day, time) -- `w.fresh_slot(doctor)` hands them out -- and its own phone number
(`w.fresh_phone()`).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import itertools
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _clinic_app import clinic_app  # noqa: E402
from _conversation import _LANG_CODE, Call  # noqa: E402
from _pod_stubs import pod_stubs  # noqa: E402
from test_orchestrator_persona import ASRResult, FakeTTS, FakeWS  # noqa: E402

CHAMBER_SLOT_MIN = 15


class World:
    def __init__(self, m, app, lang: str):
        self.m = m
        self.app = app
        self.lang = lang
        self.taken = self._seeded_appointments()
        self._tmp = tempfile.mkdtemp(prefix="world-")
        self._phone = itertools.count(9_800_000_000, 7)
        self._used: dict[str, int] = {}
        self._doctor_cache: dict[str, dict] = {}

    @staticmethod
    def _seeded_appointments() -> set[tuple[str, str, str]]:
        """(doctor, day, time) the clinic already holds before any call -- its own sample patients' appointment. A
        matrix that allocated one of these would be refused by the clinic, rightly, and call that a failure."""
        import db as clinic_db  # the clinic's own modules, loaded by clinic_app
        import models as clinic_models

        session = clinic_db.SessionLocal()
        try:
            out = set()
            for a in session.query(clinic_models.Appointment).all():
                doctor = session.get(clinic_models.Doctor, a.doctor_id)
                if doctor is not None:
                    out.add((doctor.name, a.date, a.time_slot))
            return out
        finally:
            session.close()

    # ------------------------------------------------------------------ calls
    def call(self, lang: str | None = None) -> Call:
        import httpx

        from agent.tools_client import ClinicToolsClient

        m, lang = self.m, lang or self.lang
        tools = ClinicToolsClient("http://clinic.test")
        tools._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://clinic.test"
        )
        state = {"text": "", "lang": _LANG_CODE[lang], "data": {}}

        async def route(session, wav):
            result = ASRResult(state["text"], state.get("agreement", 0.9))
            result.decoder_used = state.get("decoder")
            return state["lang"], result

        async def resolve(session, text, lng):
            return {"secondary_intent": None, "direct_reply_bn": None, **state["data"]}

        async def fetch_catalogue():
            # The lay-term path (a belly ultrasound said as "পেটের আলট্রাসাউন্ড") reads the catalogue over HTTP from
            # CLINIC_API_BASE, which here is nobody; the same clinic app answers instead.
            r = await tools._client.get("/api/v1/catalogue")
            r.raise_for_status()
            return r.json()

        m._admission = None
        m._tts_router = FakeTTS()
        m.CONDITION_INPUT = "off"
        m._tools = tools
        m._route_and_transcribe = route
        m._resolve_intent = resolve
        m._fetch_catalogue = fetch_catalogue
        m._catalogue_cache.update({"payload": None, "at": 0.0})

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        session = m.CallSession(FakeWS())
        session.release_gate()
        session.disclosed_langs.add(_LANG_CODE[lang])
        session.lang = _LANG_CODE[lang]
        session.call_state = m.new_call_state()
        call = Call(m, session, state, tempfile.mkdtemp(dir=self._tmp), loop)
        call._tools_client = tools
        return call

    def close(self, call: Call) -> None:
        with contextlib.suppress(Exception):
            call.session.cleanup()
        with contextlib.suppress(Exception):
            call._loop.run_until_complete(call._tools_client._client.aclose())
        with contextlib.suppress(Exception):
            call._loop.close()
        asyncio.set_event_loop(None)

    # ------------------------------------------------------------------ the clinic's own facts
    def clinic(self, call: Call, coro_fn, *args):
        return call._run(coro_fn(*args))

    # ------------------------------------------------------------------ unique things
    def fresh_phone(self) -> str:
        return str(next(self._phone))

    def fresh_index(self, key: str) -> int:
        n = self._used.get(key, 0)
        self._used[key] = n + 1
        return n


@contextlib.contextmanager
def world(lang: str = "bn"):
    with pod_stubs(REPO_ROOT) as imp:
        m = imp("main_pcm")
        with clinic_app(sample_patients=True) as (app, client):
            saved = {k: getattr(m, k) for k in (
                "_admission", "_tts_router", "CONDITION_INPUT", "_tools", "_route_and_transcribe", "_resolve_intent",
                "_fetch_catalogue",
            )}
            try:
                yield World(m, app, lang)
            finally:
                for k, v in saved.items():
                    setattr(m, k, v)
                asyncio.set_event_loop(None)


def next_chamber_dates(chamber_days: list[int], count: int, start: dt.date | None = None) -> list[dt.date]:
    """The next `count` dates on which a doctor with `chamber_days` (Monday = 0) sits, from tomorrow on."""
    day = (start or dt.date.today()) + dt.timedelta(days=1)
    out: list[dt.date] = []
    while len(out) < count:
        if day.weekday() in chamber_days:
            out.append(day)
        day += dt.timedelta(days=1)
    return out
