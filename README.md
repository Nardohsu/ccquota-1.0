# ccquota

A usage status line for [Claude Code](https://claude.com/claude-code). One Python file, no dependencies, no network calls.

```
ctx 5%  5h 87% 1h30m  wk 12% 6d9h  today 4.1M  ●7
```

At a glance: how full this session's context is, where the 5-hour and weekly limits stand and when they reset, how many tokens today has cost, and how many Claude Code sessions are actually running.

The quota figures are the real ones. Claude Code hands its status line a live `rate_limits` object on every invocation, so `5h` and `wk` are the same numbers `/usage` shows, not an estimate.

## Install

Requires Python 3.7+. Nothing else.

```bash
git clone https://github.com/YOUR_NAME/ccquota.git
```

Then point Claude Code at it in `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python /path/to/ccquota/ccquota.py",
    "refreshInterval": 300
  }
}
```

On Windows, backslashes need escaping in JSON: `"python C:\\Users\\you\\ccquota\\ccquota.py"`.

`refreshInterval` (seconds, minimum 1) re-runs the line on a timer. It **adds** a timer rather than replacing the event triggers, so a sent message still updates the line immediately; the timer only covers the idle stretches. Without it, a window left open while you work elsewhere shows a frozen number. At roughly 60-100 ms a run, 300 seconds costs nothing worth measuring and 10 seconds costs about 1% of one core. Anything under 5 seconds is wasted, since quota does not move that fast.

Preview it without wiring anything up:

```bash
python ccquota.py --test
```

**The desktop app did not run status line commands** when this was tested, on the 2.1.247 build it ships with. It reads `settings.json` - plugins and hooks from the same file take effect - but the `statusLine` key was ignored, verified by a command that was never invoked across a clean restart with trust accepted and hooks enabled, while the same setup worked immediately in the CLI. The desktop app bundles its own Claude Code build separate from the one on your PATH, so check your own version before assuming this still holds. Use the CLI if the line does not appear.

## Panel

The status line is one dense line. `ccquota_panel.py` lays the same data out for
reading, in a small always-on-top window: both limit bars with their reset times,
this session's context and cache, today's tokens split by model, what has been
using your limits over the last 24 hours, and which Claude Code processes are
running.

```bash
python ccquota_panel.py
```

On Windows, `panel.bat` opens it without a console window. It refreshes itself
every 30 seconds and has a manual refresh button.

Only the status line is handed live figures by Claude Code, so the panel reads a
snapshot the status line writes on each invocation. Keep a CLI session running
with ccquota installed and the panel stays current; with no snapshot it falls
back to the cached figures on disk and labels the source at the bottom, so a
stale number is never presented as a live one.

Requires tkinter, which ships with python.org builds and most distributions
(`apt install python3-tk` on Debian and Ubuntu).

## Segments

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

Green below 70%, yellow from 70%, red from 90%.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CCQUOTA_SEGMENTS` | `ctx,5h,wk,today,agents` | Which segments to show, in order |
| `CCQUOTA_SEP` | two spaces | Separator between segments |
| `CCQUOTA_CTX_LIMIT` | unset | Denominator for `ctx` when the host sends no context window |
| `CCQUOTA_SESSION_MAX_AGE` | `86400` | Ignore session files older than this many seconds |
| `CCQUOTA_COLOR` | — | Set to `0` to disable colour |
| `NO_COLOR` | — | Also disables colour ([no-color.org](https://no-color.org)) |
| `CCQUOTA_DEBUG` | — | Raise exceptions instead of degrading quietly |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Honoured if you have moved Claude Code's config |

## How the numbers are worked out

**The host payload is the authority.** Claude Code pipes a JSON object into the status line command on every run. It carries `rate_limits.five_hour` and `rate_limits.seven_day` with a live `used_percentage` and `resets_at`, a `context_window` with the real `context_window_size` and `used_percentage`, plus `prompt_cache`, `cost`, `model` and `workspace`. Anything available there is used directly — no estimation, no staleness. Everything below exists for hosts too old to send it.

**First fallback: the cached figures on disk.** Claude Code stores the `/usage` response under `cachedUsageUtilization` in its global config. It refreshes at startup — **opening the `/usage` panel does not refresh it**, verified against a cache that stayed eight days stale while the panel showed current figures — so a long-running session drifts. A percentage only describes the window it was measured in, so it is used only while that window is still open.

**Second fallback: the transcripts.** Token counts derived locally, marked with `~` so an estimate never passes for a reported figure. The weekly reset rolls forward on its fixed 7-day cadence; the 5-hour window cannot, because it opens on your first message rather than on a clock grid — the reset times carry minutes and sub-seconds — so it is found by looking for the first message after a gap of five hours or more. Checked against a live panel, this derivation put the reset within two minutes of the official one.

**The config file is chosen by content.** More than one file can be called `.claude.json`: Claude Code keeps a small bootstrap file in the config directory and the real one in `$HOME`, and which is which has changed between versions. Picking the first that exists lets the decoy win and the quota data vanish, so ccquota picks the one that actually carries the figures.

**Responses are deduplicated**, for `today` and the transcript fallbacks. A single API response is often written as several lines that share a message id and repeat the same usage object; summing them overstates usage by more than 100% — measured at 136% over a week of real data and 149% over a day. Entries are keyed on message id plus request id, largest value wins, which also picks up the final figure when a streamed response grew while being written.

**Cache reads are excluded** from those token counts. `cache_read_input_tokens` runs roughly 30x larger than everything else, because every turn re-reads the whole conversation.

**Liveness is asked of the OS.** The heartbeat in `sessions/*.json` is far too sparse to detect a running process, and the files are not cleaned up when a session is killed, so ccquota checks whether the PID is alive. PIDs get reused, so session files older than a day are ignored.

**Work is deferred until a segment needs it.** The transcript sweep and the 70 KB config parse only happen if a segment falls back to them, so a payload-only line costs about 60 ms instead of 100 ms.

**The transcript cache is incremental.** Transcripts are append-only, so only bytes added since the last run are parsed. On a machine with 326 MB of recent transcripts that is the difference between 0.6 s and 0.04 s of sweep. The cache lives at `<config-dir>/ccquota/cache.json` and holds eight days; delete it any time to rebuild.

## Limitations

- `today` counts transcripts on this machine only. Cloud sessions and other devices are not included.
- Anything marked `~` is a local estimate on a host that sent no figures. The official percentage is weighted by model, so local token counts do not convert to it at a fixed rate: one measurement on a pure-Opus 5-hour window put 56% at 933k tokens.
- If a status line is configured but hooks are disabled, Claude Code skips it entirely. That is the host's behaviour, not a bug here.

## Privacy

Reads local files, writes one cache file, and makes no network requests. It parses token counts and timestamps out of your transcripts; it never reads message content, and never touches your credentials.

## License

MIT

---

# 繁體中文

Claude Code 的用量狀態列。單一 Python 檔、零相依套件、不連網。

```
ctx 5%  5h 87% 1h30m  wk 12% 6d9h  today 4.1M  ●7
```

一眼看完：這個 session 的 context 用了多少、5 小時窗與週窗各到哪又何時重置、今日燒了多少 token，以及有幾個 Claude Code 真的在跑。

額度數字是**真的官方數字**。Claude Code 每次呼叫狀態列時都會餵進一個即時的 `rate_limits`，所以 `5h` 和 `wk` 跟 `/usage` 看到的一樣，不是估算。

## 安裝

需要 Python 3.7 以上。把 repo 抓下來後，在 `~/.claude/settings.json` 指過去：

```json
{
  "statusLine": {
    "type": "command",
    "command": "python C:\\path\\to\\ccquota\\ccquota.py",
    "refreshInterval": 300
  }
}
```

Windows 路徑的反斜線在 JSON 裡要跳脫。先預覽不接設定：`python ccquota.py --test`。

`refreshInterval`（單位秒，最小 1）讓狀態列另外按計時器重跑。它是**疊加**、不是取代事件觸發 —— 送出訊息一樣立刻更新，計時器只負責閒置的空檔。不設的話，你開著視窗卻在別處工作時數字會凍住。單次約 60~100 毫秒，設 300 秒完全不值一提，設 10 秒約吃掉 1% 的一個核心；低於 5 秒是浪費，額度不會變那麼快。

**桌面版在測試當下不執行狀態列指令**（它內建的是 2.1.247 版）。它會讀 `settings.json`（同一個檔案裡的 plugins 和 hooks 都正常生效），但 `statusLine` 這個 key 被忽略 —— 實測在 trust 已接受、hooks 已啟用的情況下乾淨重開，指令從未被呼叫，而同一份設定在 CLI 立刻就生效。桌面版自帶一份跟你 PATH 上不同的 Claude Code，所以先確認自己的版本再假設這個結論仍然成立。那行沒出現就改用 CLI。

## 彈窗面板

狀態列是壓縮成一行的資訊。`ccquota_panel.py` 把同一份資料攤開給人看，做成一個
小的置頂視窗：兩條額度進度條與重置時間、本次 session 的 context 與快取命中、
今日 token 依模型拆分、近 24 小時是什麼在用你的額度，以及有哪些 Claude Code
進程在跑。

```bash
python ccquota_panel.py
```

Windows 上雙擊 `panel.bat` 可以不帶主控台視窗開啟。每 30 秒自動刷新，右上角也有
手動更新鈕。

只有狀態列會拿到 Claude Code 餵的即時數字，所以彈窗讀的是狀態列每次執行時留下
的快照。保持一個裝了 ccquota 的 CLI session 開著，彈窗的數字就一直是新的；沒有
快照時會退回磁碟上的快取數字，並在底部標明來源 —— 過期的數字不會被當成即時的呈現。

需要 tkinter（python.org 的安裝版與多數發行版都內建；Debian/Ubuntu 用
`apt install python3-tk`）。

## 數字是怎麼算出來的

**stdin payload 是權威來源。** Claude Code 每次執行狀態列都會用管線餵進一包 JSON，裡面有 `rate_limits.five_hour` 和 `rate_limits.seven_day`（含即時的 `used_percentage` 與 `resets_at`）、`context_window`（含真實的 `context_window_size`）、還有 `prompt_cache`、`cost`、`model`、`workspace`。凡是那裡有的就直接用 —— 不估算、不會過期。以下所有機制都只是給太舊、不送這包資料的版本用的。

**第一層退路：磁碟上的快取數字。** Claude Code 把 `/usage` 的回應存在全域設定的 `cachedUsageUtilization`。它在**啟動時**刷新 —— **開 `/usage` 面板並不會刷新它**，實測面板顯示當下數字時快取仍停在 8 天前 —— 所以長時間執行的 session 會漂掉。百分比只描述它被量測的那個窗，因此只在該窗還沒結束時採用。

**第二層退路：transcript。** 從本機推算 token，標上 `~`，讓估算值絕不會被誤認成官方數字。週重置可以按固定 7 天週期往前推；5 小時窗不行，因為它從你的第一則訊息開始算、不對齊時鐘格（重置時間帶著分鐘和小數秒），所以改成找「間隔 5 小時以上之後的第一則訊息」。跟即時面板對照過，這個推導算出的重置時間跟官方只差 2 分鐘。

**設定檔依內容挑選。** 叫做 `.claude.json` 的檔案不只一個：Claude Code 在設定目錄放一個小的開機檔，真正的那份在 `$HOME`，而且版本之間換過位置。挑「第一個存在的」會讓假檔勝出、額度資料整個消失，所以挑真的帶著數字的那一份。

**回應要去重**（用於 `today` 與上述退路）。一次 API 回應常被寫成多行，共用同一個 message id、每行重複同一份 usage；直接加總會高估 100% 以上 —— 實測一週高估 136%、單日 149%。以 message id 加 request id 為 key、取最大值，串流過程中長大的數字也才會取到最終值。

**不計 cache 讀取。** `cache_read_input_tokens` 大約是其他項目的 30 倍，因為每一輪都重讀整段對話。

**存活狀態問作業系統。** `sessions/*.json` 裡的心跳太稀疏，判斷不了進程還在不在，而且 session 被強制關掉時檔案不會清掉，所以直接檢查 PID 是否存活；PID 會被重用，超過一天的 session 檔一律忽略。

**用不到就不做。** transcript 掃描與 70 KB 設定檔解析都只在真的有 segment 退到那一層時才執行，所以純 payload 的狀態列約 60 毫秒，而非 100 毫秒。

**transcript 快取是增量的。** transcript 是 append-only，只解析上次之後新增的位元組。在近期 transcript 有 326 MB 的機器上，這是掃描 0.6 秒與 0.04 秒的差別。快取在 `<設定目錄>/ccquota/cache.json`，保留 8 天，隨時刪掉都能重建。

## 限制

- `today` 只統計這台機器上的 transcript，雲端 session 與其他裝置不算在內。
- 標著 `~` 的是宿主沒送數字時的本機估算。官方百分比會依模型加權，所以本機 token 數無法用固定比例換算：實測一個純 Opus 的 5 小時窗，56% 對應 933k token。
- 設了狀態列但關掉 hooks 的話，Claude Code 會整個跳過狀態列。那是宿主行為，不是這支程式的問題。

## 隱私

只讀本機檔案、只寫一個快取檔、完全不連網。它從 transcript 解析 token 數與時間戳，不讀訊息內容，也不碰任何憑證。

## 授權

MIT
