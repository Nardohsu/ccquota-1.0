# ccquota

A local usage dashboard for [Claude Code](https://claude.com/claude-code). It shows how
much of your 5-hour and weekly quota is left and when each resets, what today has cost,
and how many Claude Code sessions are actually running — all read from files Claude Code
already writes on your machine. No network calls, no pip packages.

It comes in two forms over one shared data layer:

**A panel** — a dark pixel-adventure guild dashboard. Weekly quota as a stamina bar, a
camp scene showing your running sessions, and the full statistics one click behind it.

**A status line** — the same figures compressed into the line under the Claude Code
prompt:

```
ctx 5%  5h 87% 1h30m  wk 12% 6d9h  today 4.1M  ●7
```

The quota figures are the real ones. Claude Code hands its status line a live
`rate_limits` object on every invocation, so `5h` and `wk` are the same numbers `/usage`
shows, not an estimate.

## Install

Requires Python 3.7+. The panel also needs tkinter, which ships with python.org builds
and most distributions (`apt install python3-tk` on Debian and Ubuntu).

```bash
git clone https://github.com/Nardohsu/ccquota-1.0.git
```

That is the whole installation — there is nothing to build and nothing to pip install.

## Run

### The panel

```bash
python ccquota_panel.py
```

On Windows, double-click `panel.bat` to open it without a console window.

### The status line

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

**The desktop app did not run status line commands** when this was tested, on the 2.1.247
build it ships with. It reads `settings.json` - plugins and hooks from the same file take
effect - but the `statusLine` key was ignored, verified by a command that was never
invoked across a clean restart with trust accepted and hooks enabled, while the same
setup worked immediately in the CLI. The desktop app bundles its own Claude Code build
separate from the one on your PATH, so check your own version before assuming this still
holds. Use the CLI if the line does not appear.

`refreshInterval` (seconds, minimum 1) re-runs the line on a timer. It **adds** a timer
rather than replacing the event triggers, so a sent message still updates the line
immediately; the timer only covers the idle stretches. Without it, a window left open
while you work elsewhere shows a frozen number. At roughly 60-100 ms a run, 300 seconds
costs nothing worth measuring and 10 seconds costs about 1% of one core. Anything under
5 seconds is wasted, since quota does not move that fast.

### Checking it works

```bash
python -m unittest discover -p "test_*.py"
```

## Panel

The three cards show remaining weekly quota, local tokens from the last seven days, and
today's local tokens. The camp scene shows the active Claude Code session count. Click
the expedition area, press Ctrl+D, or choose **選項 → 用量明細** to open the full
statistics in a scrollable window: both limits, context and cache, model breakdown,
attribution, and running sessions.

The guild asset count is explicitly a rolling seven-day token count, not a lifetime total
or a currency balance. Unknown official quota is shown as a dash; local estimates are
never converted into an official percentage.

The panel refreshes every 30 seconds and collects in the background so the window stays
responsive. F5 refreshes, Ctrl+D opens details, and **選項 → 視窗保持置頂** toggles
always-on-top.

Scenes are chosen randomly from the `assets` directory at startup and at local system
time **09:00 and 18:00** daily. **選項 → 隨機切換場景** switches manually. Each change
fades the old scene out and the new one in over two seconds; text and controls remain
visible. The next image differs from the current one when another valid image is
available. Supported formats: PNG, GIF (first frame), PPM and PGM. Add images directly to
`assets`; the folder is rescanned on every switch. Unreadable images are skipped. With
one valid image it stays in place; with no images the dashboard still works.

Scheduling runs inside the panel, so keep it open for automatic changes. After sleep, the
panel catches up once if a 09:00/18:00 boundary was missed; it does not replay every
missed change. Midnight does not trigger a change. Scene transitions use
`ccquota_scene.py`, Tkinter and the Python standard library, with no extra packages or
network access.

Only the status line is handed live figures by Claude Code, so the panel reads a snapshot
the status line writes on each invocation. Keep a CLI session running with ccquota
installed and the panel stays current; with no snapshot it falls back to the cached
figures on disk and labels the source at the bottom, so a stale number is never presented
as a live one.

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

**First fallback: the cached figures on disk.** Claude Code stores the `/usage` response under `cachedUsageUtilization` in its global config. It refreshes rarely and on no schedule this project could pin down: one observed gap was eight days, another ran past forty hours of daily use across several app restarts and CLI sessions without moving. **Opening the `/usage` panel does not refresh it** - verified against a cache that stayed eight days stale while the panel showed current figures. A percentage only describes the window it was measured in, so it is used only while that window is still open. That makes it far more useful for the weekly limit, whose window is seven days long, than for the 5-hour one, where any cached figure is dead within five hours; treat it as a floor, since usage has grown since it was taken.

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

Claude Code 的本機用量儀表板。看 5 小時窗與週窗還剩多少、各自何時重置、今天燒了多少
token，以及實際有幾個 Claude Code 在跑 —— 全部讀自 Claude Code 本來就寫在你機器上的
檔案，不連網、不裝任何 pip 套件。

同一套資料層，兩種呈現方式：

**面板** —— 深藍像素冒險公會介面。週額度做成體力條、營火場景顯示執行中的 session
數量，完整統計藏在一次點擊之後。

**狀態列** —— 同樣的數字壓縮成 Claude Code 輸入框下方的一行：

```
ctx 5%  5h 87% 1h30m  wk 12% 6d9h  today 4.1M  ●7
```

額度數字是**真的官方數字**。Claude Code 每次呼叫狀態列時都會餵進一個即時的
`rate_limits`，所以 `5h` 和 `wk` 跟 `/usage` 看到的一樣，不是估算。

## 安裝

需要 Python 3.7 以上。面板另外需要 tkinter（python.org 的安裝版與多數發行版都內建；
Debian/Ubuntu 用 `apt install python3-tk`）。

```bash
git clone https://github.com/Nardohsu/ccquota-1.0.git
```

抓下來就是全部的安裝了 —— 不用編譯，也沒有要 pip install 的東西。

## 執行

### 面板

```bash
python ccquota_panel.py
```

Windows 上雙擊 `panel.bat` 可以不帶主控台視窗開啟。

### 狀態列

在 `~/.claude/settings.json` 指過去，然後重開 Claude Code：

```json
{
  "statusLine": {
    "type": "command",
    "command": "python C:\\path\\to\\ccquota\\ccquota.py",
    "refreshInterval": 300
  }
}
```

Windows 路徑的反斜線在 JSON 裡要跳脫。想先看看長相而不接設定：

```bash
python ccquota.py --test
```

**桌面版在測試當下不執行狀態列指令**（它內建的是 2.1.247 版）。它會讀 `settings.json`
（同一個檔案裡的 plugins 和 hooks 都正常生效），但 `statusLine` 這個 key 被忽略 ——
實測在 trust 已接受、hooks 已啟用的情況下乾淨重開，指令從未被呼叫，而同一份設定在
CLI 立刻就生效。桌面版自帶一份跟你 PATH 上不同的 Claude Code，所以先確認自己的版本再
假設這個結論仍然成立。那行沒出現就改用 CLI。

`refreshInterval`（單位秒，最小 1）讓狀態列另外按計時器重跑。它是**疊加**、不是取代事件
觸發 —— 送出訊息一樣立刻更新，計時器只負責閒置的空檔。不設的話，你開著視窗卻在別處工作
時數字會凍住。單次約 60~100 毫秒，設 300 秒完全不值一提，設 10 秒約吃掉 1% 的一個核心；
低於 5 秒是浪費，額度不會變那麼快。

### 確認能動

```bash
python -m unittest discover -p "test_*.py"
```

## 面板細節

主畫面四個區塊：

- **每週行動力**：官方週額度的剩餘百分比、重置時間與分段體力條。
- **公會總資產**：本機近 7 日 token（不含快取讀取），不是歷史累計或貨幣。
- **今日採集**：今日 token 與請求數。
- **遠征隊伍**：像素村莊營火場景與執行中的 Claude Code session 數量。

點擊隊伍區、按 Ctrl+D，或選擇「選項 → 用量明細」，可查看原本的全部統計：
5 小時／每週限制、Context、快取、模型拆分、近 24 小時用量歸屬及工作階段。
明細視窗支援捲動。沒有官方額度時顯示「—」，不把本機 token 估算換成百分比。

每 30 秒自動刷新，右上角也有手動更新鈕，也可按 F5。資料在背景讀取，不會阻塞操作；
失敗時顯示錯誤與重試按鈕。從「選項 → 視窗保持置頂」切換置頂。

### 場景隨機切換

- 啟動時從 `assets` 隨機選圖；每天依電腦本機時間 **09:00、18:00** 自動換圖。
- 從「選項 → 隨機切換場景」可立即預覽切換效果。
- 每次約 **2 秒**：舊圖漸暗淡出，新圖再漸亮淡入，文字與按鈕不受影響。
- 有其他有效圖片時，不連續重複目前圖片。全部圖片共用同一個隨機候選池。
- 將 PNG、GIF（第一幀）、PPM 或 PGM 直接放在 `assets`，下次切換就會重新掃描。
  損壞的圖片會略過；只有一張時維持原圖，沒有圖片時仍可查看用量。
- 自動換景需要面板保持開啟。休眠錯過時間點時，喚醒後補切一次，不連續重播；
  午夜不換景。排程每秒檢查一次，因此通常會在整點後一秒內開始轉場。

場景功能位於 `ccquota_scene.py`，使用 Tkinter 與 Python 標準函式庫，
不新增執行期相依套件、不連網、不修改原始圖片。

只有狀態列會拿到 Claude Code 餵的即時數字，所以彈窗讀的是狀態列每次執行時留下
的快照。保持一個裝了 ccquota 的 CLI session 開著，彈窗的數字就一直是新的；沒有
快照時會退回磁碟上的快取數字，並在底部標明來源 —— 過期的數字不會被當成即時的呈現。

## 數字是怎麼算出來的

**stdin payload 是權威來源。** Claude Code 每次執行狀態列都會用管線餵進一包 JSON，裡面有 `rate_limits.five_hour` 和 `rate_limits.seven_day`（含即時的 `used_percentage` 與 `resets_at`）、`context_window`（含真實的 `context_window_size`）、還有 `prompt_cache`、`cost`、`model`、`workspace`。凡是那裡有的就直接用 —— 不估算、不會過期。以下所有機制都只是給太舊、不送這包資料的版本用的。

**第一層退路：磁碟上的快取數字。** Claude Code 把 `/usage` 的回應存在全域設定的 `cachedUsageUtilization`。它很少刷新，而且本專案找不出固定規律：觀察到的一次間隔是 8 天，另一次則是超過 40 小時的日常使用、期間重開過數次桌面版也跑過 CLI，仍然沒有更新。**開 `/usage` 面板並不會刷新它** —— 實測面板顯示當下數字時快取仍停在 8 天前。百分比只描述它被量測的那個窗，因此只在該窗還沒結束時採用。這讓它對**週限制**遠比對 5 小時窗有用（週窗長達 7 天，5 小時窗的任何快取數字 5 小時內必定作廢）；並且要當成**下限**看，因為量測之後用量只會增加。

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
