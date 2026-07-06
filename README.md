# usage-status.py — Claude Code Status Line

Shows at a glance in the Claude Code status line:

- **Agent · Model** (e.g. `claude · Sonnet 5`)
- **Session limit** (rolling 5-hour window) with progress bar and reset time
- **Weekly limit** (rolling 7-day window) with progress bar and reset time
- **Extra usage credits** (if enabled on your plan)

The data comes from the official Anthropic OAuth usage endpoint (the same numbers as under *Settings → Usage*) and is cached locally for 60 seconds so the status line's 1-second refresh doesn't hammer the API.

## Requirements

- macOS (the script reads the access token from the Keychain via the `security` command)
- Python 3 (no extra packages needed, standard library only)
- Claude Code, logged in via the regular OAuth login (Claude subscription, not a pure API-key setup)

## Installation

1. **Place the script**

   Copy the script to:

   ```
   ~/.claude/scripts/usage-status.py
   ```

   If you install it somewhere else, adjust the path in step 2 accordingly.

2. **Configure the status line in Claude Code**

   Open (or create) `~/.claude/settings.json` and add or set the `statusLine` block:

   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "python3 /Users/kai_schwendig/.claude/scripts/usage-status.py",
       "refreshInterval": 1
     }
   }
   ```

   Important: `command` must contain the absolute path to the script.

3. **Restart Claude Code**

   Restart a running Claude Code session (or open a new terminal window) so the new status line configuration gets loaded.

That's it — the status line should now appear automatically.

## Optional: show an agent name

The script reads an `agent` field from `~/.claude/settings.json` (default: `claude`). To display a custom name (e.g. for multiple parallel setups), add:

```json
{
  "agent": "my-agent-name",
  "statusLine": { ... }
}
```

## Troubleshooting

- **"Usage: not available"** is shown:
  - Check that you're logged into Claude Code (OAuth login, not just an API key).
  - Check that the `Claude Code-credentials` entry exists in the macOS Keychain:
    ```
    security find-generic-password -s "Claude Code-credentials" -w
    ```
  - If macOS shows a Keychain access prompt the first time, confirm it once ("Always Allow").

- **Reset the cache**: cached values live in `~/.claude/scripts/.usage-cache.json`. The file can be deleted if needed and will be recreated automatically.

- **Wrong model shown**: the script reads the most recently used model from the newest transcript file under `~/.claude/projects/**/*.jsonl`. With multiple parallel sessions, the most recently active one is always shown.
