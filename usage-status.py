#!/usr/bin/env python3
"""Claude Code status line — agent, model, and live account usage limits.

Shows the real subscription limits (same as Settings → Usage):
  - Current session  (5-hour window)
  - Current week     (7-day, all models)

Data is fetched from the Anthropic OAuth usage endpoint using the access token
stored in the macOS keychain, and cached locally for CACHE_TTL seconds so the
1-second status-line refresh does not hammer the API.
"""

import json
import os
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

SETTINGS_FILE = Path.home() / ".claude" / "settings.json"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
CACHE_FILE = Path.home() / ".claude" / "scripts" / ".usage-cache.json"

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
CACHE_TTL = 60  # seconds — how long to reuse a fetched API response
BAR_WIDTH = 10

# short display names for model IDs
MODEL_NAMES = {
    "claude-opus-4-8":           "Opus 4.8",
    "claude-opus-4-7":           "Opus 4.7",
    "claude-sonnet-4-6":         "Sonnet 4.6",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
    "claude-fable-5":            "Fable 5",
}


# ── helpers ───────────────────────────────────────────────────────────────────
def bar(percent, width=BAR_WIDTH):
    frac = max(0.0, min(1.0, percent / 100.0))
    filled = round(frac * width)
    return "▓" * filled + "░" * (width - filled)


def get_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def fmt_reset(iso_str):
    """Format an ISO reset timestamp into local 'HH:MM' or 'DD.MM.' if far off."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local = dt.astimezone()  # convert to system local timezone
        now = datetime.now(timezone.utc).astimezone()
        delta = local - now
        if delta.total_seconds() < 0:
            return "now"
        if delta.total_seconds() < 24 * 3600:
            return local.strftime("%H:%M")
        return local.strftime("%d.%m. %H:%M")
    except (ValueError, AttributeError):
        return "?"


# ── model / agent (from live transcript + settings) ─────────────────────────────
def get_model_and_agent(cfg):
    agent = cfg.get("agent") or "claude"
    model_id = ""
    best_file, best_mtime = None, 0.0
    try:
        for proj_dir in PROJECTS_DIR.iterdir():
            if not proj_dir.is_dir() or "observer" in proj_dir.name:
                continue
            for jf in proj_dir.glob("*.jsonl"):
                try:
                    mt = jf.stat().st_mtime
                    if mt > best_mtime:
                        best_mtime, best_file = mt, jf
                except OSError:
                    continue
    except OSError:
        pass

    if best_file:
        try:
            with open(best_file, encoding="utf-8") as f:
                lines = f.readlines()
            for raw in reversed(lines):
                raw = raw.strip()
                if not raw or '"assistant"' not in raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "assistant":
                    continue
                m = obj.get("message", {}).get("model", "")
                if m and m != "<synthetic>":
                    model_id = m
                    break
        except OSError:
            pass

    display = MODEL_NAMES.get(
        model_id,
        model_id.replace("claude-", "").replace("-", " ").title() if model_id else "–",
    )
    return display, agent


# ── usage limits (cached API fetch) ─────────────────────────────────────────────
def read_token():
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
        return data.get("claudeAiOauth", {}).get("accessToken")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None


def fetch_usage():
    token = read_token()
    if not token:
        return None
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-code-statusline",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None


def get_usage_cached():
    """Return usage dict, using a local cache valid for CACHE_TTL seconds."""
    now = time.time()
    # try cache first
    try:
        cached = json.loads(CACHE_FILE.read_text())
        if now - cached.get("_fetched_at", 0) < CACHE_TTL:
            return cached.get("data")
    except (OSError, json.JSONDecodeError):
        cached = None

    # refresh
    data = fetch_usage()
    if data is not None:
        try:
            CACHE_FILE.write_text(json.dumps({"_fetched_at": now, "data": data}))
        except OSError:
            pass
        return data

    # fetch failed → fall back to stale cache if present
    try:
        return json.loads(CACHE_FILE.read_text()).get("data")
    except (OSError, json.JSONDecodeError):
        return None


# ── render ──────────────────────────────────────────────────────────────────────
def main():
    cfg = get_settings()
    model_display, agent = get_model_and_agent(cfg)
    usage = get_usage_cached()

    head = f"{agent} · {model_display}"

    if not usage:
        print(f"{head}  │  Usage: nicht verfügbar")
        return

    five = usage.get("five_hour") or {}
    seven = usage.get("seven_day") or {}

    s_pct = five.get("utilization", 0)
    w_pct = seven.get("utilization", 0)
    s_reset = fmt_reset(five.get("resets_at", ""))
    w_reset = fmt_reset(seven.get("resets_at", ""))

    parts = [
        head,
        f"Session {bar(s_pct)} {s_pct:.0f}% (↻{s_reset})",
        f"Week {bar(w_pct)} {w_pct:.0f}% (↻{w_reset})",
    ]

    # optional: extra-usage credits (EUR), only if enabled
    extra = usage.get("extra_usage") or {}
    if extra.get("is_enabled"):
        ep = extra.get("utilization", 0)
        used = extra.get("used_credits", 0)
        lim = extra.get("monthly_limit", 0)
        cur = extra.get("currency", "")
        parts.append(f"Extra {ep:.0f}% ({used:.0f}/{lim} {cur})")

    print("  │  ".join(parts))


if __name__ == "__main__":
    main()
