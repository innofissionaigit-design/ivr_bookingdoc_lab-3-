"""The language rollback lever is one setting everywhere (upgrade prompt section 9), and unreviewed
pronunciations are off by default in code AND in deployment (section 7). docs/adr/0002.

Static and shell-level checks: nothing here needs a pod, a model or a network.

    python -m pytest tests/test_language_rollback_and_drafts.py -v
"""

import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def bash(script):
    """Run a bash snippet from the repo root; skip where there is no usable bash."""
    exe = shutil.which("bash")
    if not exe:
        pytest.skip("no bash on this machine")
    try:
        r = subprocess.run([exe, "-c", script], cwd=REPO, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("bash could not be run")
    if r.returncode != 0 and not r.stdout.strip():
        pytest.skip(f"bash is not usable here: {r.stderr.strip()[:80]}")
    return r.stdout.strip()


# ------------------------------------------------------------ pronunciation drafts (section 7)


def test_drafts_are_off_in_code_by_default(monkeypatch):
    from agent import pronunciation

    monkeypatch.delenv("PRONUNCIATION_ALLOW_DRAFT", raising=False)
    assert not pronunciation.drafts_enabled()
    assert not pronunciation.speakable("sugar", "bn")  # a draft: silent until a native listener approves it
    assert pronunciation.speakable("blood", "bn")  # an established translation, not a draft


def test_the_toggle_is_explicit_and_turns_the_drafts_on(monkeypatch):
    from agent import pronunciation

    monkeypatch.setenv("PRONUNCIATION_ALLOW_DRAFT", "1")
    assert pronunciation.drafts_enabled() and pronunciation.speakable("sugar", "bn")
    monkeypatch.setenv("PRONUNCIATION_ALLOW_DRAFT", "0")
    assert not pronunciation.drafts_enabled()


def test_deployment_does_not_turn_the_drafts_on():
    env = read("deploy", "env.sh")
    assert re.search(r"^export PRONUNCIATION_ALLOW_DRAFT=\$\{PRONUNCIATION_ALLOW_DRAFT:-0\}$", env, re.M)


def test_sourcing_env_sh_leaves_the_drafts_off_unless_asked():
    assert bash("unset PRONUNCIATION_ALLOW_DRAFT; source deploy/env.sh; echo $PRONUNCIATION_ALLOW_DRAFT") == "0"
    assert bash("PRONUNCIATION_ALLOW_DRAFT=1; source deploy/env.sh; echo $PRONUNCIATION_ALLOW_DRAFT") == "1"


# ----------------------------------------------------------------- the rollback lever (section 9)


def test_the_default_build_is_three_languages_and_one_variable_rolls_it_back():
    assert bash("unset VOICE_AGENT_LANGUAGES; source deploy/env.sh; echo $VOICE_AGENT_LANGUAGES") == "bn,hi,en"
    assert bash("VOICE_AGENT_LANGUAGES=bn; source deploy/env.sh; echo $VOICE_AGENT_LANGUAGES") == "bn"


def test_start_all_starts_the_english_asr_only_when_english_is_active():
    script = read("deploy", "start_all.sh")
    case = script[script.index('case ",${VOICE_AGENT_LANGUAGES},"') :]
    case = case[: case.index("esac")]
    assert "*,en,*)" in case and "start english-asr 8003" in case.split("*,en,*)")[1].split(";;")[0]
    off = case.split("*)")[-1]
    assert "start english-asr" not in off, "the Bengali-only branch must not start it"
    assert "fuser -k 8003/tcp" in off, "...and must stop a copy left over from the three-language build (by port)"


def test_the_gating_logic_behaves_as_written():
    case = (
        'case ",${V}," in *,en,*) echo starts;; *) echo skipped;; esac'  # the same pattern start_all.sh uses
    )
    assert [bash(f"V={v}; {case}") for v in ("bn", "bn,hi", "bn,hi,en", "bn,en")] == [
        "skipped", "skipped", "starts", "starts",
    ]


def test_a_forced_restart_is_possible_so_a_changed_setting_actually_takes_effect():
    script = read("deploy", "start_all.sh")
    assert '"${FORCE_RESTART:-0}" != "1"' in script
    assert "FORCE_RESTART=1" in read("deploy", "set_languages.sh")


def test_set_languages_refuses_a_value_that_would_take_the_stack_down():
    # these exit before anything is written or restarted, so they are safe to run anywhere
    for bad in ("hi", "bn,fr", "en"):
        r = bash(f'bash deploy/set_languages.sh {bad} 2>&1; echo "exit=$?"')
        assert "refusing" in r and r.endswith("exit=2"), (bad, r)


def test_the_tts_server_loads_only_the_active_voices():
    src = read("tts_server.py")
    assert "parse_active_languages(os.environ.get(\"VOICE_AGENT_LANGUAGES\"))" in src
    assert 'SUPPORTED_LANGUAGES = ("bn", "hi", "en")' not in src


def test_the_orchestrator_and_the_tts_server_read_the_variable_the_same_way():
    from agent.lid import parse_active_languages

    assert 'parse_active_languages(os.environ.get("VOICE_AGENT_LANGUAGES"))' in read("main.py")
    for raw in (None, "bn", "bn,hi", "bn,hi,en", "en"):
        assert "bn" in parse_active_languages(raw)


def test_status_reports_the_build_it_is_looking_at():
    s = read("deploy", "status.sh")
    assert "VOICE_AGENT_LANGUAGES" in s and "english-asr" in s


def test_the_pcm_variant_carries_the_same_language_wiring_as_main():
    # main_pcm.py is generated from main.py; a stale copy would silently lose these.
    for needle in ("early_lid.resolve_early", "note_turn_outcome", "REPLY_LANGUAGE_MODE", "_maybe_start_early_lid"):
        assert needle in read("main.py") and needle in read("main_pcm.py"), needle
