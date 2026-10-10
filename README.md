# ccquota

**English** · [繁體中文](README.zh-Hant.md)

A local usage dashboard for [Claude Code](https://claude.com/claude-code), and for Codex
too. It shows how much of your 5-hour and weekly quota is left and when each resets, how
many tokens today has used, and how many Claude Code sessions are actually running.
Everything comes from files Claude Code already writes on your machine. Python standard
library only: nothing to build, nothing to pip install.

The quota figures are the official ones, the same numbers `/usage` shows, not estimates.

## Three ways to see it

| | Where it appears | Works in |
|---|---|---|
| [**Mod**](#mod) | A band above the prompt | Desktop app Code tab and terminal |
| [**Status line**](#status-line) | The line under the prompt | Terminal (CLI) only |
| [**Panel**](#panel) | Its own window, a pixel-art guild dashboard | Anywhere |

```
Mod          5h 87% 2h40m  ·  週 31%  ·  ctx 12%  ·  cache 96%  ·  今日 2.9M  ·  $3.20  ·  ● 3  ·  Codex 5h 34% 週 49%
Status line  ctx 5%  5h 87% 1h30m  wk 12% 6d9h  today 4.1M  ●7
```

If you use the desktop app, start with the mod. The desktop app does not run status line
commands.

## Install

Requires Python 3.7+. The panel also needs tkinter, which ships with the python.org
installers and most distributions (`apt install python3-tk` on Debian and Ubuntu).

```bash
git clone https://github.com/Nardohsu/ccquota.git
```

That is the whole installation. Then set up whichever of the three you want.

## Mod

This repository is also a Claude Code mod that draws a band above the prompt, in the desktop Code tab
and in the terminal alike. Type `/ccquota` to hide or show it.

### Install from GitHub

In a terminal `claude` session (this command is not available in the desktop Code tab):

```
/plugin install ccquota --marketplace Nardohsu/ccquota
```

Answer `y` to add the marketplace, then choose the **user** scope; at that scope it also
loads in the sessions the desktop app starts. The install includes `ccquota.py`, so the
settings screen can be left as it is. Change `python` there (or later in `/config`) if
Python 3 is not on your PATH as `python`, for example `python3`. Run
`claude plugin update` to pick up new versions.

### Or run it from your clone

To have your own edits take effect without reinstalling, name the repository folder in
the `env` block of `~/.claude/settings.json`:

```json
"env": {
  "CLAUDE_CODE_PLUGIN_DIRS": "/path/to/ccquota"
}
```

Every session started after that loads it, in the desktop app and the terminal alike.
Conversations that were already open load it once they are restarted. For a single
terminal session, `claude --plugin-dir /path/to/ccquota` does the same. Use one
of these or the marketplace install, not both, or the band is drawn twice.

### What the band shows

| Part | Meaning | Source |
|---|---|---|
| `5h` / `週` | 5-hour and weekly quota used, time to reset | The engine itself, live |
| `ctx` | This conversation's context fill | The engine itself |
| `cache` | Prompt cache hit rate of this conversation; `cold` when the last turn missed | Summed from each finished turn |
| `今日` | Tokens since local midnight, all projects | `ccquota.py --local` |
| `$` | This conversation's cost | The engine itself |
| `●` | Claude Code processes running | `ccquota.py --local` |
| `Codex` | Codex 5-hour and weekly quota (see [Codex](#codex)) | `ccquota.py --local` |

Green below 70%, yellow from 70%, red from 90%. For `cache` it is the other way round:
a low hit rate is the warning, because every miss re-sends the whole context.

## Status line

Point Claude Code at it in `~/.claude/settings.json`, then restart Claude Code:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python /path/to/ccquota/ccquota.py",
    "refreshInterval": 300
  }
}
```

On Windows, backslashes need escaping in JSON:
`"python C:\\Users\\you\\ccquota\\ccquota.py"`.

To see the line without wiring anything up:

```bash
python ccquota.py --test
```

`refreshInterval` (seconds, minimum 1) re-runs the line on a timer as well. It adds to the
event triggers rather than replacing them, so sending a message still updates the line at
once; the timer only covers idle stretches, when the number would otherwise freeze. A run
takes about 60 to 100 ms, so 300 seconds costs nothing measurable and 10 seconds about 1%
of one core. Under 5 seconds is wasted, as quota does not move that fast.

The desktop app ignored the `statusLine` key when this was tested on its bundled 2.1.247
build, while the same setup worked at once in the CLI. The desktop app ships its own
Claude Code build, separate from the one on your PATH, so check your version; the mod
works in both.

### Segments

Pick and order them with `CCQUOTA_SEGMENTS` (default: `ctx,5h,wk,today,agents`).

| Segment | Shows | Source |
|---|---|---|
| `ctx` | This session's context, as a percentage of the real window | Host payload |
| `5h` | The 5-hour limit and time to reset | Host payload |
| `wk` | The weekly limit and time to reset | Host payload |
| `cache` | Prompt cache hit ratio, with `*` when the cache is cold | Host payload |
| `cost` | This session's cost in USD | Host payload |
| `dir` | Current working directory | Host payload |
| `model` | Model display name | Host payload |
| `today` | Tokens since local midnight, across every project | Transcripts |
| `agents` | Number of Claude Code processes running | `sessions/*.json` plus a liveness check |
| `cx` | Codex 5-hour and weekly limits | Codex app-server, else `~/.codex/sessions` |

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CCQUOTA_SEGMENTS` | `ctx,5h,wk,today,agents` | Which segments to show, in order |
| `CCQUOTA_SEP` | two spaces | Separator between segments |
| `CCQUOTA_CTX_LIMIT` | unset | Denominator for `ctx` when the host sends no context window |
| `CCQUOTA_SESSION_MAX_AGE` | `86400` | Ignore session files older than this many seconds |
| `CCQUOTA_COLOR` | unset | Set to `0` to disable colour |
| `NO_COLOR` | unset | Also disables colour ([no-color.org](https://no-color.org)) |
| `CCQUOTA_DEBUG` | unset | Raise exceptions instead of degrading quietly |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Honoured if you have moved Claude Code's config |
| `CODEX_CLI_PATH` | unset | Path to `codex.exe`, if it is not in the usual place |

## Panel

```bash
python ccquota_panel.py
```

On Windows, double-click `panel.bat` to open it without a console window.

The main window has four parts:

- **Weekly stamina**: remaining official weekly quota, its reset time, and a segmented bar.
- **Guild assets**: local tokens over the last seven days, excluding cache reads. A rolling
  count, not a lifetime total or a currency.
- **Today's harvest**: today's tokens and request count.
- **Expedition**: a pixel camp scene and the number of Claude Code sessions running.

Click the expedition area, press Ctrl+D, or choose **選項 → 用量明細** (Options → Usage
details) to open the full statistics in a scrollable window: both limits, context and
cache, model breakdown, attribution over the last 24 hours, running sessions, and Codex.
Unknown official quota is shown as a dash; local estimates are never turned into an
official percentage.

The panel refreshes every 30 seconds in the background, so the window stays responsive;
F5 refreshes at once, and errors come with a retry button. **選項 → 視窗保持置頂**
toggles always-on-top. Ctrl+D and F5 only work while the panel window has focus.

Claude Code hands live quota figures only to the status line, so the panel reads a
snapshot the status line leaves on each run. Keep a CLI session with the status line
running and the panel stays current. Without a snapshot it falls back to the cached
figures on disk and names the source at the bottom, so an old number is never shown as
a live one.

### Scenes

- At startup, and every day at **09:00 and 18:00** local time, a scene is picked at random
  from `assets`. **選項 → 隨機切換場景** switches by hand.
- Each change fades the old scene out and the new one in over about two seconds; text
  and buttons stay put. The same image is not picked twice in a row when there is another.
- Add PNG, GIF (first frame), PPM or PGM files to `assets`; the folder is rescanned on
  every switch. Unreadable images are skipped. With one image it stays; with none the
  dashboard still works.
- Changes only happen while the panel is open. After sleep it catches up once if it
  missed 09:00 or 18:00, rather than replaying every change. Midnight changes nothing.

## Codex

ccquota also shows your Codex (OpenAI) 5-hour and weekly limits: in the mod band, in the
panel's usage details, and as the `cx` status line segment. `python ccquota.py --codex`
prints them as JSON.

The figures come live from Codex's own local app-server. ccquota starts
`codex.exe app-server`, asks `account/rateLimits/read`, and closes it again. That takes
about a second, and the result is reused for 60 seconds. It uses the desktop app's
bundled `codex.exe` (the newest `%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe`) or the
one named in `CODEX_CLI_PATH`; the npm `codex.cmd` shim is not used.

This is an experimental Codex API. When it fails, ccquota falls back to the last figures
in `~/.codex/sessions`, marked `~` as possibly stale, and leaves out any window that has
already reset. Those logs only change while Codex runs, so they can be days old. Without
Codex installed, nothing about Codex appears.

The approach follows [codex-usage-companion](https://github.com/gkfriend/codex-usage-companion) (MIT); no code is taken from it.

## Checking it works

```bash
python -m unittest discover -p "test_*.py"
claude plugin validate .
claude plugin test .
```

## How the numbers are worked out

**The host is the authority.** Claude Code pipes a JSON object into the status line
command on every run, and the mod reads the same figures from the engine. They carry the
5-hour and weekly `used_percentage` and `resets_at`, the real context window size, prompt
cache, cost, model and workspace. Anything available there is used directly: no
estimation, no staleness. Everything below is a fallback for when it is not.

**First fallback: the cached figures on disk.** Claude Code stores the `/usage` response
under `cachedUsageUtilization` in its global config. It refreshes rarely and on no
schedule this project could pin down: one observed gap was eight days, another ran past
forty hours of daily use across several restarts without moving. **Opening `/usage` does
not refresh it.** A percentage only describes the window it was measured in, so it is
used only while that window is still open. That makes it useful for the weekly limit and
nearly useless for the 5-hour one. Treat it as a floor, since usage has only grown since.

**Second fallback: the transcripts.** Token counts derived locally, marked `~` so an
estimate never passes for an official figure. The weekly reset rolls forward on its
fixed 7-day cadence. The 5-hour window cannot, because it opens on your first message
rather than on a clock grid, so it is found by looking for the first message after a gap
of five hours or more. Checked against a live panel, this put the reset within two
minutes of the official one.

**The config file is chosen by content.** More than one file can be called
`.claude.json`: a small bootstrap file in the config directory and the real one in
`$HOME`, and which is where has changed between versions. Taking the first that exists
lets the decoy win, so ccquota takes the one that actually carries the figures.

**Responses are deduplicated.** One API response is often written as several lines that
share a message id and repeat the same usage; summing them overstates usage by more
than 100% (measured at 136% over a week and 149% over a day). Entries are keyed on
message id plus request id and the largest value wins, which also catches the final
figure of a streamed response.

**Cache reads are excluded** from token counts. `cache_read_input_tokens` runs about 30
times larger than everything else, because every turn re-reads the whole conversation.

**Liveness is asked of the OS.** The heartbeat in `sessions/*.json` is too sparse to tell
whether a process is running, and the files stay behind when a session is killed, so
ccquota checks whether the PID is alive. PIDs get reused, so session files older than a
day are ignored.

**Work is deferred until needed.** The transcript sweep and the 70 KB config parse only
run when a segment falls back to them, so a status line served from the host payload
takes about 60 ms instead of 100 ms.

**The transcript cache is incremental.** Transcripts are append-only, so only bytes added
since the last run are parsed: on a machine with 326 MB of recent transcripts, 0.04 s
instead of 0.6 s. The cache lives in `<config-dir>/ccquota/` and holds eight days;
delete it any time to rebuild.

## Limitations

- `today` counts transcripts on this machine only. Cloud sessions and other devices are
  not included.
- Anything marked `~` is an estimate. The official percentage is weighted by model, so
  local token counts do not convert to it at a fixed rate: one pure-Opus 5-hour window
  read 56% at 933k tokens.
- If a status line is configured but hooks are disabled, Claude Code skips the status
  line entirely. That is the host's behaviour.
- The Codex figures rely on an experimental Codex API and may need updating when Codex
  changes it.

## Privacy

ccquota reads local files, writes its own cache files, and makes no network requests
itself. It parses token counts and timestamps out of your transcripts and never reads
message content or your credentials. For the Codex figures it starts your local
`codex.exe app-server`, which asks OpenAI for your limits using Codex's own sign-in;
ccquota never reads that sign-in and does not store your account id.

## License

MIT
