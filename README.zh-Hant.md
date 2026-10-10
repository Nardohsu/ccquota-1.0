# ccquota

[English](README.md) · **繁體中文**

[Claude Code](https://claude.com/claude-code) 的本機用量儀表板，也能看 Codex。它告訴你 5 小時
和每週額度還剩多少、什麼時候重置、今天用了多少 token，以及實際有幾個 Claude Code 在跑。
所有數字都來自 Claude Code 本來就寫在你電腦上的檔案。只用 Python 標準函式庫，不用編譯，
也不用 pip install。

額度數字是官方數字，跟 `/usage` 看到的一樣，不是估算。

## 三種看法

| | 出現在哪裡 | 適用 |
|---|---|---|
| [**Mod**](#mod) | 輸入框上方的一條橫條 | 桌面版 Code 分頁、終端機 |
| [**狀態列**](#狀態列) | 輸入框下方的一行 | 只有終端機（CLI） |
| [**面板**](#面板) | 獨立視窗，像素風冒險公會介面 | 任何地方 |

```
Mod     5h 87% 2h40m  ·  週 31%  ·  ctx 12%  ·  cache 96%  ·  今日 2.9M  ·  $3.20  ·  ● 3  ·  Codex 5h 34% 週 49%
狀態列  ctx 5%  5h 87% 1h30m  wk 12% 6d9h  today 4.1M  ●7
```

用桌面版的話，從 Mod 開始。桌面版不會執行狀態列指令。

## 安裝

需要 Python 3.7 以上。面板另外需要 tkinter，python.org 的安裝版和多數 Linux 發行版都內建
（Debian、Ubuntu 用 `apt install python3-tk`）。

```bash
git clone https://github.com/Nardohsu/ccquota.git
```

這樣就裝好了。接著依需要設定下面三種之一。

## Mod

`mod/` 是一個 Claude Code mod，在輸入框上方畫一條橫條，桌面版 Code 分頁和終端機都能用。
輸入 `/ccquota` 可以隱藏或顯示橫條。

### 從 GitHub 安裝

在終端機的 `claude` 裡輸入（桌面版 Code 分頁沒有這個指令）：

```
/plugin install ccquota --marketplace Nardohsu/ccquota
```

詢問是否加入 marketplace 時回答 `y`，範圍選 **user**，這樣桌面版開的對話也會載入。設定畫面
中，把 `script` 改成你自己的 `ccquota.py` 路徑（預設值是作者的路徑），`python` 改成你的
Python 3 指令，之後也可以在 `/config` 修改。有新版時執行 `claude plugin update`。

### 或從自己的 clone 執行

想讓自己改的檔案不必重裝就生效，在 `~/.claude/settings.json` 的 `env` 區塊指向 `mod`
資料夾：

```json
"env": {
  "CLAUDE_CODE_PLUGIN_DIRS": "/path/to/ccquota/mod"
}
```

之後新開的每個對話都會載入，桌面版和終端機都一樣。已經開著的對話要重開才會載入。只想在
單一終端機對話使用，可以改用 `claude --plugin-dir /path/to/ccquota/mod`。這兩種和
marketplace 安裝只能擇一，同時使用橫條會畫兩次。

### 橫條上的每一段

| 欄位 | 意思 | 來源 |
|---|---|---|
| `5h` / `週` | 5 小時與每週額度用了多少、多久後重置 | Claude Code 引擎，即時 |
| `ctx` | 這個對話的上下文用量 | Claude Code 引擎 |
| `cache` | 這個對話的快取命中率；上一輪沒命中時標 `cold` | 每輪結束時累加 |
| `今日` | 今天零點以來所有專案的 token | `ccquota.py --local` |
| `$` | 這個對話的花費 | Claude Code 引擎 |
| `●` | 正在執行的 Claude Code 程序數 | `ccquota.py --local` |
| `Codex` | Codex 的 5 小時與每週額度（見 [Codex](#codex)） | `ccquota.py --local` |

低於 70% 綠色，70% 起黃色，90% 起紅色。`cache` 相反，命中率低才是警訊，因為每次沒命中
都要把整段上下文重送一次。

## 狀態列

在 `~/.claude/settings.json` 加上這段，然後重開 Claude Code：

```json
{
  "statusLine": {
    "type": "command",
    "command": "python /path/to/ccquota/ccquota.py",
    "refreshInterval": 300
  }
}
```

Windows 路徑的反斜線在 JSON 裡要寫兩次：
`"python C:\\Users\\you\\ccquota\\ccquota.py"`。

不接設定，只想先看看長相：

```bash
python ccquota.py --test
```

`refreshInterval`（單位秒，最小 1）讓狀態列另外按計時器重跑。它是疊加在事件觸發上，不是
取代，所以送出訊息一樣會立刻更新；計時器只負責閒置的時候，不然數字會停住不動。執行一次約
60 到 100 毫秒，設 300 秒幾乎沒有負擔，設 10 秒約佔一個 CPU 核心的 1%。低於 5 秒沒有意義，
額度不會變那麼快。

實測桌面版內建的 2.1.247 版會忽略 `statusLine` 設定，同一份設定在 CLI 則立刻生效。桌面版
自帶一份 Claude Code，跟你 PATH 上的那份不同，請以你自己的版本為準。Mod 在兩邊都能用。

### 區段

用 `CCQUOTA_SEGMENTS` 選擇要顯示哪些區段和順序（預設 `ctx,5h,wk,today,agents`）。

| 區段 | 顯示內容 | 來源 |
|---|---|---|
| `ctx` | 這個對話的上下文佔真實視窗的百分比 | Claude Code 傳入的資料 |
| `5h` | 5 小時額度與重置倒數 | Claude Code 傳入的資料 |
| `wk` | 每週額度與重置倒數 | Claude Code 傳入的資料 |
| `cache` | 快取命中率，快取失效時加 `*` | Claude Code 傳入的資料 |
| `cost` | 這個對話的花費（美元） | Claude Code 傳入的資料 |
| `dir` | 目前的工作目錄 | Claude Code 傳入的資料 |
| `model` | 模型名稱 | Claude Code 傳入的資料 |
| `today` | 今天零點以來所有專案的 token | 對話紀錄 |
| `agents` | 正在執行的 Claude Code 程序數 | `sessions/*.json` 加上程序存活檢查 |
| `cx` | Codex 的 5 小時與每週額度 | Codex app-server，失敗時讀 `~/.codex/sessions` |

### 環境變數

| 變數 | 預設 | 用途 |
|---|---|---|
| `CCQUOTA_SEGMENTS` | `ctx,5h,wk,today,agents` | 要顯示的區段與順序 |
| `CCQUOTA_SEP` | 兩個空格 | 區段之間的分隔字元 |
| `CCQUOTA_CTX_LIMIT` | 不設 | Claude Code 沒傳上下文視窗大小時，`ctx` 的分母 |
| `CCQUOTA_SESSION_MAX_AGE` | `86400` | 超過這麼多秒的 session 檔不計 |
| `CCQUOTA_COLOR` | 不設 | 設成 `0` 關閉顏色 |
| `NO_COLOR` | 不設 | 同樣會關閉顏色（[no-color.org](https://no-color.org)） |
| `CCQUOTA_DEBUG` | 不設 | 出錯時直接拋出例外，不安靜降級 |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Claude Code 設定目錄搬過位置時使用 |
| `CODEX_CLI_PATH` | 不設 | `codex.exe` 不在預設位置時，指定它的路徑 |

## 面板

```bash
python ccquota_panel.py
```

Windows 上雙擊 `panel.bat`，可以不帶主控台視窗開啟。

主畫面有四個區塊：

- **每週行動力**：官方週額度的剩餘百分比、重置時間與分段體力條。
- **公會總資產**：本機近 7 天的 token（不含快取讀取），是滾動統計，不是歷史累計或貨幣。
- **今日採集**：今天的 token 與請求數。
- **遠征隊伍**：像素營地場景，以及執行中的 Claude Code 數量。

點遠征隊伍那一區、按 Ctrl+D，或選「選項 → 用量明細」，可以開啟完整統計：5 小時與每週
額度、上下文、快取、各模型用量、近 24 小時用量歸屬、執行中的對話，以及 Codex。明細視窗
可以捲動。沒有官方額度時顯示「—」，不會把本機估算換算成官方百分比。

面板每 30 秒在背景自動更新，操作不會卡住；按 F5 立即更新，讀取失敗時會顯示錯誤和重試
按鈕。「選項 → 視窗保持置頂」切換是否置頂。Ctrl+D 和 F5 要在面板視窗取得焦點時才有作用。

Claude Code 只把即時額度交給狀態列，所以面板讀的是狀態列每次執行時留下的快照。只要有一個
開著狀態列的 CLI 對話，面板的數字就會保持最新。沒有快照時，面板改用磁碟上的快取數字，並在
底部標明來源，舊數字不會被當成即時數字顯示。

### 場景切換

- 啟動時，以及每天本機時間 **09:00、18:00**，會從 `assets` 隨機挑一張場景。「選項 →
  隨機切換場景」可以手動切換。
- 每次切換約 2 秒，舊圖淡出、新圖淡入，文字和按鈕不受影響。有其他圖可選時，不會連續
  挑到同一張。
- 把 PNG、GIF（只取第一幀）、PPM 或 PGM 放進 `assets` 就好，每次切換都會重新掃描資料夾。
  讀不了的圖會跳過；只有一張時就一直用那張，沒有圖也照樣能看用量。
- 面板要開著才會自動換圖。電腦休眠錯過 09:00 或 18:00 時，喚醒後只補換一次，不會一路
  重播。午夜不換圖。

## Codex

ccquota 也會顯示 Codex（OpenAI）的 5 小時與每週額度，出現在 mod 橫條、面板的用量明細，
以及狀態列的 `cx` 區段。`python ccquota.py --codex` 會以 JSON 輸出。

數字直接來自 Codex 本機的 app-server：ccquota 啟動 `codex.exe app-server`，送出
`account/rateLimits/read` 查詢後就關掉它。整個過程約一秒，結果會沿用 60 秒。它使用桌面版
內附的 `codex.exe`（`%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe` 中最新的一個），或
`CODEX_CLI_PATH` 指定的路徑，不使用 npm 的 `codex.cmd`。

這是 Codex 的實驗性 API。查詢失敗時，ccquota 改讀 `~/.codex/sessions` 裡最後一筆紀錄，標上
`~` 表示可能過時，已經重置的額度直接不顯示。這些紀錄只在 Codex 執行時更新，可能是好幾天前
的數字。沒裝 Codex 的話，不會出現任何 Codex 的資訊。

做法參考 [codex-usage-companion](https://github.com/gkfriend/codex-usage-companion)（MIT），沒有使用它的程式碼。

## 確認能正常運作

```bash
python -m unittest discover -p "test_*.py"
claude plugin validate mod
claude plugin test mod
```

## 數字是怎麼算出來的

**以 Claude Code 給的數字為準。** Claude Code 每次執行狀態列時都會傳入一包 JSON，mod 也
從引擎讀到同樣的數字。裡面有 5 小時和每週額度的 `used_percentage` 與 `resets_at`、真實的
上下文視窗大小、快取、花費、模型和工作目錄。拿得到的就直接用，不估算，也不會過時。以下都
是拿不到時的退路。

**第一層退路：磁碟上的快取數字。** Claude Code 把 `/usage` 的回應存在全域設定的
`cachedUsageUtilization`。它很少更新，而且找不出規律：觀察到一次隔了 8 天才更新，另一次
連續使用超過 40 小時、重開好幾次都沒變。**開 `/usage` 不會讓它更新。** 百分比只對量測當時
的那個時段有效，所以只在那個時段還沒結束時採用。這讓它對每週額度有用，對 5 小時額度幾乎
沒用。而且要把它當成下限看，因為量測之後用量只會增加。

**第二層退路：對話紀錄。** 從本機紀錄推算 token，標上 `~`，估算值不會被當成官方數字。每週
重置可以按固定的 7 天週期往後推。5 小時的時段不行，因為它從你的第一則訊息開始算，不對齊
整點，所以改成找「間隔 5 小時以上之後的第一則訊息」。跟即時面板對照，推算出的重置時間和
官方只差 2 分鐘。

**設定檔依內容挑選。** 叫做 `.claude.json` 的檔案不只一個：設定目錄裡有一個小的開機檔，
真正的那份在 `$HOME`，而且不同版本放的位置不一樣。直接拿第一個找到的，可能會拿到開機檔，
額度資料就整個不見，所以 ccquota 挑真的有數字的那一份。

**同一次回應只算一次。** 一次 API 回應常被寫成好幾行，共用同一個 message id，每行都重複
同一份用量。直接加總會多算一倍以上（實測一週多算 136%，單日 149%）。所以用 message id
加 request id 當 key、取最大值，串流回應寫到一半時也能拿到最後的數字。

**不計快取讀取。** `cache_read_input_tokens` 大約是其他項目的 30 倍，因為每一輪都會重讀
整段對話。

**程序是否還在跑，直接問作業系統。** `sessions/*.json` 的心跳間隔太長，判斷不了程序還在
不在，而且對話被強制關掉時檔案不會清掉，所以 ccquota 直接檢查 PID 是否存活。PID 會被
重複使用，所以超過一天的 session 檔一律不計。

**用不到就不做。** 掃描對話紀錄和解析 70 KB 的設定檔，只在某個區段真的要退到那一層時才做。
數字都由 Claude Code 提供時，狀態列約 60 毫秒，不是 100 毫秒。

**對話紀錄的快取是增量的。** 對話紀錄只會往後追加，所以只解析上次之後新增的部分。在近期
紀錄有 326 MB 的電腦上，掃描從 0.6 秒降到 0.04 秒。快取放在 `<設定目錄>/ccquota/`，保留
8 天，隨時刪掉都能重建。

## 限制

- `今日` 和 `today` 只統計這台電腦上的對話紀錄，雲端對話和其他裝置不算。
- 標 `~` 的都是估算。官方百分比會依模型加權，本機 token 數沒辦法用固定比例換算：實測一個
  全用 Opus 的 5 小時時段，56% 對應 933k token。
- 設了狀態列但關掉 hooks 的話，Claude Code 會直接跳過狀態列，這是 Claude Code 本身的行為。
- Codex 的數字依賴 Codex 的實驗性 API，Codex 改版後可能要跟著修改。

## 隱私

ccquota 只讀本機檔案、只寫自己的快取檔，本身不連網。它從對話紀錄解析 token 數和時間，
不讀訊息內容，也不碰你的帳號憑證。Codex 的數字是啟動本機的 `codex.exe app-server`，由它用
Codex 自己的登入向 OpenAI 查詢；ccquota 不讀那份登入資料，也不儲存你的帳號 ID。

## 授權

MIT
