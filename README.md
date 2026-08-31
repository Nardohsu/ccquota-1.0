# ccquota

A usage status line for [Claude Code](https://claude.com/claude-code). One Python file, no dependencies, no network calls.

```
ctx 216k  5h 358k 4h22m  wk 53% 6d12h  today 2.9M  ●4
```

At a glance: how full this session's context is, how much you have burned in the current 5-hour window and when it resets, where the weekly quota stands, today's total, and how many Claude Code sessions are actually running.

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
    "command": "python /path/to/ccquota/ccquota.py"
  }
}
```

On Windows, remember that backslashes need escaping in JSON:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python C:\\Users\\you\\ccquota\\ccquota.py"
  }
}
```

Preview it without wiring anything up:

```bash
python ccquota.py --test
```

The first run builds a cache and takes about a second. Every run after that is around 100 ms.

## Segments

Pick and order them with `CCQUOTA_SEGMENTS` (default: `ctx,5h,wk,today,agents`).

| Segment | Shows | Source |
|---|---|---|
| `ctx` | This session's context size, as a percentage if you set a limit | The last response in this session's transcript |
| `5h` | Tokens used in the live 5-hour window, and time to reset | Official cache when current, otherwise derived from transcripts |
| `wk` | Weekly quota percentage and time to reset | Official cache when current, otherwise a `~` estimate |
| `today` | Tokens since local midnight | Transcripts |
| `agents` | Number of Claude Code processes running | `sessions/*.json` plus a liveness check |
| `dir` | Current working directory | Passed in by Claude Code |
| `model` | Model display name | Passed in by Claude Code |
| `cost` | This session's cost in USD | Passed in by Claude Code |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CCQUOTA_SEGMENTS` | `ctx,5h,wk,today,agents` | Which segments to show, in order |
| `CCQUOTA_CTX_LIMIT` | unset | Set it to turn `ctx` from a token count into a percentage |
| `CCQUOTA_SEP` | two spaces | Separator between segments |
| `CCQUOTA_SESSION_MAX_AGE` | `86400` | Ignore session files older than this many seconds |
| `CCQUOTA_COLOR` | — | Set to `0` to disable colour |
| `NO_COLOR` | — | Also disables colour ([no-color.org](https://no-color.org)) |
| `CCQUOTA_DEBUG` | — | Raise exceptions instead of degrading quietly |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Honoured if you have moved Claude Code's config |

Green below 70%, yellow from 70%, red from 90%.

## How the numbers are worked out

Everything comes from files Claude Code already writes on your machine. Getting them right took some care, and the details are worth knowing before you trust a number.

**The official percentage is real, but it goes stale.** Claude Code caches the `/usage` response under `cachedUsageUtilization` in its global config, including `percent` and `resets_at` for each limit. That is the same data the `/usage` screen shows. It only refreshes when Claude Code itself fetches it, though, so it can sit untouched for days. A percentage only describes the window it was measured in, so ccquota shows it **only while that window is still open** and falls back to a local estimate — marked with `~` — once `resets_at` has passed.

**Weekly reset times survive going stale; the 5-hour one does not.** The weekly window runs on a fixed 7-day cadence, so an expired `resets_at` can be rolled forward to the current window and stays correct. The 5-hour window cannot: it opens when you send your first message after the previous one lapsed, not on a clock grid — the reset times Claude Code reports carry minutes and sub-seconds. So the live 5-hour window is derived from your transcripts by finding the first message after a gap of five hours or more.

**Responses are deduplicated.** A single API response is often written to the transcript as several lines that share a message id and each repeat the same usage object. Summing the lines overstates usage by more than 100% — measured at 136% over a week of real data and 149% over a day. Entries are keyed on message id plus request id, and the largest value wins, which also picks up the final figure when a streamed response grew while it was being written.

**Cache reads are excluded.** `cache_read_input_tokens` runs roughly 30x larger than everything else, because every turn re-reads the whole conversation. Including it produces a big impressive number that tracks nothing useful, so the token figures here are input + output + cache creation.

**Liveness is asked of the OS.** The heartbeat written into `sessions/*.json` is far too sparse to detect a running process, and those files are not cleaned up when a session is killed. ccquota checks whether the PID is alive instead. PIDs get reused, so session files older than a day are ignored.

**The cache is incremental.** Transcripts are append-only, so only bytes added since the last run are parsed. On a machine with 326 MB of recent transcripts this is the difference between 0.6 s and 0.1 s per refresh. The cache lives at `<config-dir>/ccquota/cache.json` and holds eight days of history; delete it any time to rebuild.

## Limitations

- Anything shown with `~` is estimated from local transcripts, not reported by Anthropic. Open `/usage` to refresh the authoritative numbers.
- Token counts cover transcripts on this machine only. Cloud sessions and other devices are not included.
- `ctx` reports raw tokens because the real context limit varies by model and by `--autocompact`, and is not recorded on disk. Set `CCQUOTA_CTX_LIMIT` to your own window to get a percentage.
- If a status line is configured but hooks are disabled, Claude Code skips it entirely. That is the host's behaviour, not a bug here.

## Privacy

Reads local files, writes one cache file, and makes no network requests. It parses token counts and timestamps out of your transcripts; it never reads message content, and never touches your credentials.

## License

MIT

---

# 繁體中文

Claude Code 的用量狀態列。單一 Python 檔、零相依套件、不連網。

```
ctx 216k  5h 358k 4h22m  wk 53% 6d12h  today 2.9M  ●4
```

一眼看完：這個 session 的 context 用了多少、目前 5 小時窗燒了多少又何時重置、週額度到哪、今日總量，以及有幾個 Claude Code 真的在跑。

## 安裝

需要 Python 3.7 以上，其他都不用。把 repo 抓下來後，在 `~/.claude/settings.json` 指過去：

```json
{
  "statusLine": {
    "type": "command",
    "command": "python C:\\path\\to\\ccquota\\ccquota.py"
  }
}
```

Windows 路徑的反斜線在 JSON 裡要跳脫。先預覽不接設定的話：`python ccquota.py --test`。

第一次跑會建快取、約 1 秒；之後每次約 100 毫秒。

## 數字是怎麼算出來的

全部來自 Claude Code 本來就寫在你機器上的檔案。幾個關鍵取捨值得先知道：

**官方百分比是真的，但會過期。** Claude Code 把 `/usage` 的回應快取在全域設定的 `cachedUsageUtilization`，含每個限制的 `percent` 與 `resets_at`，跟 `/usage` 畫面同一份資料。但它只在 Claude Code 自己去抓時才更新，可能好幾天不動。百分比只描述它被量測的那個窗，所以 ccquota **只在該窗還沒結束時**顯示官方數字，`resets_at` 一過就改用本機估算，並標上 `~`。

**週重置時間過期還能用，5 小時窗不行。** 週窗是固定 7 天週期，過期的 `resets_at` 可以往前推到當前窗、仍然正確。5 小時窗不行：它從你「前一個窗結束後的第一則訊息」開始算，不對齊時鐘格 —— Claude Code 回報的重置時間帶著分鐘和小數秒。所以 5 小時窗改從 transcript 推導，找出間隔 5 小時以上之後的第一則訊息。

**回應要去重。** 一次 API 回應常被寫成多行，共用同一個 message id、每行重複同一份 usage。直接加總會高估 100% 以上 —— 實測一週的資料高估 136%、單日 149%。因此以 message id 加 request id 為 key，取最大值（串流過程中數字會長大，取最大才是最終值）。

**不計 cache 讀取。** `cache_read_input_tokens` 大約是其他項目的 30 倍，因為每一輪都重讀整段對話。算進去會得到一個很漂亮但沒有意義的數字，所以這裡的 token 是 input + output + cache creation。

**存活狀態問作業系統。** `sessions/*.json` 裡的心跳太稀疏，判斷不了進程還在不在，而且 session 被強制關掉時檔案不會清掉。ccquota 直接檢查 PID 是否存活；PID 會被重用，所以超過一天的 session 檔一律忽略。

**快取是增量的。** transcript 是 append-only，所以只解析上次之後新增的位元組。在近期 transcript 有 326 MB 的機器上，這是每次刷新 0.6 秒與 0.1 秒的差別。快取在 `<設定目錄>/ccquota/cache.json`，保留 8 天，隨時刪掉都能重建。

## 限制

- 標著 `~` 的都是本機估算，不是 Anthropic 回報的數字。想要權威數值就開一次 `/usage` 讓快取刷新。
- 只統計這台機器上的 transcript，雲端 session 與其他裝置不算在內。
- `ctx` 顯示的是原始 token 數，因為真實 context 上限隨模型與 `--autocompact` 而變，本機沒有任何檔案記錄它。想看百分比就把你的實際上限設進 `CCQUOTA_CTX_LIMIT`。
- 設了狀態列但關掉 hooks 的話，Claude Code 會整個跳過狀態列。那是宿主行為，不是這支程式的問題。

## 隱私

只讀本機檔案、只寫一個快取檔、完全不連網。它從 transcript 解析 token 數與時間戳，不讀訊息內容，也不碰任何憑證。

## 授權

MIT
