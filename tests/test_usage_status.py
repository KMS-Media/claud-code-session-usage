"""Tests for usage-status.py.

External boundaries are mocked, never hit for real:
  - subprocess.run (macOS Keychain "security" call) -> unittest.mock.patch
  - urllib.request.urlopen (Anthropic usage API)     -> unittest.mock.patch
  - filesystem paths (SETTINGS_FILE, PROJECTS_DIR,
    CACHE_FILE)                                       -> monkeypatch to tmp_path

No real network call, no real Keychain access, ever.
"""

import json
import subprocess
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ── bar() ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "percent,expected",
    [
        (0, "░░░░░░░░░░"),
        (50, "▓▓▓▓▓░░░░░"),
        (100, "▓▓▓▓▓▓▓▓▓▓"),
        (-20, "░░░░░░░░░░"),  # clamped to 0
        (150, "▓▓▓▓▓▓▓▓▓▓"),  # clamped to 100
    ],
)
def test_bar(usage_status, percent, expected):
    assert usage_status.bar(percent) == expected


# ── fmt_reset() ───────────────────────────────────────────────────────────────

def test_fmt_reset_future_within_24h(usage_status):
    dt = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    result = usage_status.fmt_reset(dt)
    assert ":" in result and "." not in result  # "HH:MM", no date component


def test_fmt_reset_past_is_now(usage_status):
    dt = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    assert usage_status.fmt_reset(dt) == "now"


def test_fmt_reset_far_future_includes_date(usage_status):
    dt = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat().replace("+00:00", "Z")
    result = usage_status.fmt_reset(dt)
    assert "." in result  # "DD.MM. HH:MM"


def test_fmt_reset_malformed_string(usage_status):
    assert usage_status.fmt_reset("not-a-date") == "?"


def test_fmt_reset_empty_string(usage_status):
    assert usage_status.fmt_reset("") == "?"


# ── get_settings() ────────────────────────────────────────────────────────────

def test_get_settings_missing_file(usage_status, tmp_path, monkeypatch):
    monkeypatch.setattr(usage_status, "SETTINGS_FILE", tmp_path / "missing.json")
    assert usage_status.get_settings() == {}


def test_get_settings_invalid_json(usage_status, tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text("{not valid json")
    monkeypatch.setattr(usage_status, "SETTINGS_FILE", f)
    assert usage_status.get_settings() == {}


def test_get_settings_valid_json(usage_status, tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"agent": "myagent"}))
    monkeypatch.setattr(usage_status, "SETTINGS_FILE", f)
    assert usage_status.get_settings() == {"agent": "myagent"}


# ── get_model_and_agent() ─────────────────────────────────────────────────────

def _write_transcript(path, lines):
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def test_get_model_and_agent_default_agent_no_projects(usage_status, tmp_path, monkeypatch):
    monkeypatch.setattr(usage_status, "PROJECTS_DIR", tmp_path / "does-not-exist")
    display, agent = usage_status.get_model_and_agent({})
    assert agent == "claude"
    assert display == "–"


def test_get_model_and_agent_custom_agent(usage_status, tmp_path, monkeypatch):
    monkeypatch.setattr(usage_status, "PROJECTS_DIR", tmp_path / "does-not-exist")
    _, agent = usage_status.get_model_and_agent({"agent": "myagent"})
    assert agent == "myagent"


def test_get_model_and_agent_known_model(usage_status, tmp_path, monkeypatch):
    proj = tmp_path / "proj1"
    proj.mkdir()
    _write_transcript(
        proj / "session.jsonl",
        [{"type": "assistant", "message": {"model": "claude-sonnet-4-6"}}],
    )
    monkeypatch.setattr(usage_status, "PROJECTS_DIR", tmp_path)
    display, _ = usage_status.get_model_and_agent({})
    assert display == "Sonnet 4.6"


def test_get_model_and_agent_unknown_model_falls_back_to_title_case(usage_status, tmp_path, monkeypatch):
    proj = tmp_path / "proj1"
    proj.mkdir()
    _write_transcript(
        proj / "session.jsonl",
        [{"type": "assistant", "message": {"model": "claude-super-future-9"}}],
    )
    monkeypatch.setattr(usage_status, "PROJECTS_DIR", tmp_path)
    display, _ = usage_status.get_model_and_agent({})
    assert display == "Super Future 9"


def test_get_model_and_agent_skips_observer_dirs(usage_status, tmp_path, monkeypatch):
    observer = tmp_path / "observer-project"
    observer.mkdir()
    _write_transcript(
        observer / "session.jsonl",
        [{"type": "assistant", "message": {"model": "claude-opus-4-8"}}],
    )
    monkeypatch.setattr(usage_status, "PROJECTS_DIR", tmp_path)
    display, _ = usage_status.get_model_and_agent({})
    assert display == "–"  # observer dir ignored, nothing else to read


def test_get_model_and_agent_picks_newest_transcript(usage_status, tmp_path, monkeypatch):
    proj = tmp_path / "proj1"
    proj.mkdir()

    old_file = proj / "old.jsonl"
    _write_transcript(old_file, [{"type": "assistant", "message": {"model": "claude-opus-4-7"}}])

    new_file = proj / "new.jsonl"
    _write_transcript(new_file, [{"type": "assistant", "message": {"model": "claude-sonnet-4-6"}}])

    now = time.time()
    import os
    os.utime(old_file, (now - 100, now - 100))
    os.utime(new_file, (now, now))

    monkeypatch.setattr(usage_status, "PROJECTS_DIR", tmp_path)
    display, _ = usage_status.get_model_and_agent({})
    assert display == "Sonnet 4.6"


def test_get_model_and_agent_ignores_synthetic_model(usage_status, tmp_path, monkeypatch):
    proj = tmp_path / "proj1"
    proj.mkdir()
    _write_transcript(
        proj / "session.jsonl",
        [
            {"type": "assistant", "message": {"model": "<synthetic>"}},
            {"type": "assistant", "message": {"model": "claude-haiku-4-5-20251001"}},
        ],
    )
    monkeypatch.setattr(usage_status, "PROJECTS_DIR", tmp_path)
    display, _ = usage_status.get_model_and_agent({})
    assert display == "Haiku 4.5"


# ── read_token() ──────────────────────────────────────────────────────────────

def test_read_token_success(usage_status):
    fake_result = MagicMock(returncode=0, stdout=json.dumps({"claudeAiOauth": {"accessToken": "tok-123"}}))
    with patch.object(usage_status.subprocess, "run", return_value=fake_result):
        assert usage_status.read_token() == "tok-123"


def test_read_token_nonzero_returncode(usage_status):
    fake_result = MagicMock(returncode=1, stdout="")
    with patch.object(usage_status.subprocess, "run", return_value=fake_result):
        assert usage_status.read_token() is None


def test_read_token_malformed_json(usage_status):
    fake_result = MagicMock(returncode=0, stdout="not json")
    with patch.object(usage_status.subprocess, "run", return_value=fake_result):
        assert usage_status.read_token() is None


def test_read_token_subprocess_error(usage_status):
    with patch.object(usage_status.subprocess, "run", side_effect=subprocess.SubprocessError):
        assert usage_status.read_token() is None


# ── fetch_usage() ─────────────────────────────────────────────────────────────

def test_fetch_usage_no_token_short_circuits(usage_status):
    with patch.object(usage_status, "read_token", return_value=None), \
         patch.object(usage_status.urllib.request, "urlopen") as mock_urlopen:
        assert usage_status.fetch_usage() is None
        mock_urlopen.assert_not_called()


def test_fetch_usage_success(usage_status):
    payload = {"five_hour": {"utilization": 42}}
    fake_response = MagicMock()
    fake_response.read.return_value = json.dumps(payload).encode("utf-8")
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False

    with patch.object(usage_status, "read_token", return_value="tok"), \
         patch.object(usage_status.urllib.request, "urlopen", return_value=fake_response):
        assert usage_status.fetch_usage() == payload


def test_fetch_usage_network_error(usage_status):
    with patch.object(usage_status, "read_token", return_value="tok"), \
         patch.object(usage_status.urllib.request, "urlopen", side_effect=urllib.error.URLError("boom")):
        assert usage_status.fetch_usage() is None


# ── get_usage_cached() ────────────────────────────────────────────────────────

def test_get_usage_cached_fresh_cache_skips_fetch(usage_status, tmp_path, monkeypatch):
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps({"_fetched_at": time.time(), "data": {"cached": True}}))
    monkeypatch.setattr(usage_status, "CACHE_FILE", cache_file)

    with patch.object(usage_status, "fetch_usage") as mock_fetch:
        result = usage_status.get_usage_cached()
        mock_fetch.assert_not_called()
    assert result == {"cached": True}


def test_get_usage_cached_expired_cache_refetches(usage_status, tmp_path, monkeypatch):
    cache_file = tmp_path / "cache.json"
    stale_time = time.time() - usage_status.CACHE_TTL - 10
    cache_file.write_text(json.dumps({"_fetched_at": stale_time, "data": {"old": True}}))
    monkeypatch.setattr(usage_status, "CACHE_FILE", cache_file)

    with patch.object(usage_status, "fetch_usage", return_value={"fresh": True}):
        result = usage_status.get_usage_cached()
    assert result == {"fresh": True}
    assert json.loads(cache_file.read_text())["data"] == {"fresh": True}


def test_get_usage_cached_fetch_fails_falls_back_to_stale(usage_status, tmp_path, monkeypatch):
    cache_file = tmp_path / "cache.json"
    stale_time = time.time() - usage_status.CACHE_TTL - 10
    cache_file.write_text(json.dumps({"_fetched_at": stale_time, "data": {"stale": True}}))
    monkeypatch.setattr(usage_status, "CACHE_FILE", cache_file)

    with patch.object(usage_status, "fetch_usage", return_value=None):
        result = usage_status.get_usage_cached()
    assert result == {"stale": True}


def test_get_usage_cached_no_cache_and_fetch_fails(usage_status, tmp_path, monkeypatch):
    monkeypatch.setattr(usage_status, "CACHE_FILE", tmp_path / "missing.json")
    with patch.object(usage_status, "fetch_usage", return_value=None):
        assert usage_status.get_usage_cached() is None


# ── main() ────────────────────────────────────────────────────────────────────

def test_main_usage_unavailable(usage_status, capsys):
    with patch.object(usage_status, "get_settings", return_value={}), \
         patch.object(usage_status, "get_model_and_agent", return_value=("Sonnet 5", "claude")), \
         patch.object(usage_status, "get_usage_cached", return_value=None):
        usage_status.main()
    out = capsys.readouterr().out
    assert "claude · Sonnet 5" in out
    assert "nicht verfügbar" in out


def test_main_full_render(usage_status, capsys):
    usage = {
        "five_hour": {"utilization": 33, "resets_at": "2026-08-10T19:40:00Z"},
        "seven_day": {"utilization": 10, "resets_at": "2026-08-11T12:59:00Z"},
        "extra_usage": {"is_enabled": True, "utilization": 5, "currency": "EUR"},
        "spend": {
            "used": {"amount_minor": 250, "exponent": 2},
            "limit": {"amount_minor": 15000, "exponent": 2},
        },
    }
    with patch.object(usage_status, "get_settings", return_value={}), \
         patch.object(usage_status, "get_model_and_agent", return_value=("Sonnet 5", "claude")), \
         patch.object(usage_status, "get_usage_cached", return_value=usage):
        usage_status.main()
    out = capsys.readouterr().out
    assert "Session" in out and "33%" in out
    assert "Week" in out and "10%" in out
    assert "Extra 5% (2.50/150.00 EUR)" in out


def test_main_null_utilization_regression(usage_status, capsys):
    """Regression test: API returning explicit `null` utilization must not crash.

    Anthropic's /api/oauth/usage endpoint has been observed returning
    `utilization: null` for five_hour/seven_day/extra_usage instead of omitting
    the key. `.get(key, default)` only falls back on a *missing* key, not on an
    explicit `null` value — this used to raise:
        TypeError: unsupported format string passed to NoneType.__format__
    """
    usage = {
        "five_hour": {"utilization": None, "resets_at": "2026-08-10T19:40:00Z"},
        "seven_day": {"utilization": None, "resets_at": "2026-08-11T12:59:00Z"},
        "extra_usage": {"is_enabled": True, "utilization": None, "currency": "EUR"},
        "spend": {"used": {"amount_minor": None}, "limit": {"amount_minor": None}},
    }
    with patch.object(usage_status, "get_settings", return_value={}), \
         patch.object(usage_status, "get_model_and_agent", return_value=("Sonnet 5", "claude")), \
         patch.object(usage_status, "get_usage_cached", return_value=usage):
        usage_status.main()  # must not raise
    out = capsys.readouterr().out
    assert "Session" in out and "0%" in out
    assert "Week" in out
    assert "Extra 0%" in out
