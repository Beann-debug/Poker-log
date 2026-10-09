# 長片 → Shorts 自動化工具

有兩種用法：

- **全自動（建議）**：`highlights.py` 由 Gemini 從長片挑精華，一次產出多支帶字幕的 Shorts。
- **混合**：先在 Canva Highlights 手動挑片段，再用 `shorts_subs.py` 上字幕。

## 全自動：Gemini 挑精華

```
長片
 → 整支語音辨識（只跑一次，快取在 out/片名.transcript.json）
 → Gemini 讀帶時間戳的逐字稿，挑出 N 段 20–55 秒、開頭有鉤子、能獨立看懂的片段
   → out/片名.highlights.json（可改起訖時間、刪掉不要的段落）
 → 每段：剪停頓、轉 9:16、疊品牌框、燒繁中字幕 → out/片名_clips/片名_01_shorts.mp4 …
 → out/片名.titles.md：每支的標題、開場鉤子、挑選理由、爆紅分數，上傳時直接複製
```

```bash
export GEMINI_API_KEY=你的key          # Windows PowerShell：$env:GEMINI_API_KEY="你的key"
python3 highlights.py long.mp4 --count 3 --overlay frame.png

# 先只看 Gemini 挑了哪些段落，滿意再產影片
python3 highlights.py long.mp4 --select-only
python3 highlights.py long.mp4                  # 沿用剛才的選段，開始產影片

# 告訴 Gemini 你的觀眾和重點，挑得更準
python3 highlights.py long.mp4 --topic "觀眾是台灣分租房東，重點放在省錢和避免糾紛"

# 不滿意就重挑
python3 highlights.py long.mp4 --repick
```

| 參數 | 預設 | 說明 |
|---|---|---|
| `--count` | 3 | 要挑幾段 |
| `--min-len` / `--max-len` | 20 / 55 | 每段長度（秒）；剪掉停頓後通常會再短一些 |
| `--gemini-model` | gemini-2.5-flash | 可改成其他 Gemini 模型 |
| `--topic` | 無 | 補充給 Gemini 的方向 |
| `--select-only` | 關 | 只挑選段落，不產出影片 |
| `--repick` | 關 | 讓 Gemini 重新挑選 |

其餘字幕參數（`--model`、`--overlay`、`--font` 等）跟 `shorts_subs.py` 共用。

Gemini 挑出的時間會自動對齊到句子的開頭和結尾。長度不合、互相重疊的段落會被剔除。
Gemini 只會收到**逐字稿文字**，不會收到影片檔，所以成本低、速度快。

## 混合：Canva 挑片段＋腳本上字幕

Canva 負責**挑片段**和**品牌視覺**，`shorts_subs.py` 負責 Canva 做不到的部分：**繁中字幕、剪停頓、轉 9:16**。

```
長片
 → ① Canva Highlights 自動切出候選片段，挑 3–5 段 → 各自匯出 MP4
 → ② python3 shorts_subs.py 片段.mp4 --overlay frame.png
      ・語音辨識 → 繁中台灣用語 → 寫出 out/片段.subs.json
      ・剪停頓、轉 1080x1920、疊品牌框、燒字幕 → out/片段_shorts.mp4
 → ③ 看一遍；有錯字就改 subs.json 的 cues，再跑一次同一個指令（不會重新辨識）
 → ④ 上傳
```

## 安裝（第一次）

Gemini 的 API key 可以在 Google AI Studio 申請。

1. 安裝 ffmpeg
   - macOS：`brew install ffmpeg`
   - Windows：`winget install ffmpeg`
2. 安裝 Python 套件：`pip install -r requirements.txt`
3. 安裝思源黑體（Noto Sans TC），或用 `--font` 指定你電腦上的其他中文字型。

第一次執行時會自動下載語音模型（small 約 500MB），需要網路。

## 常用指令

```bash
# 基本用法
python3 shorts_subs.py clip.mp4

# 疊 Canva 品牌框＋自訂詞表
python3 shorts_subs.py clip.mp4 --overlay frame.png --glossary glossary.txt

# 準確度不夠時用比較大的模型（比較慢）
python3 shorts_subs.py clip.mp4 --model medium --retranscribe

# 不剪停頓、字幕改小一點
python3 shorts_subs.py clip.mp4 --no-cut --font-size 72
```

| 參數 | 預設 | 說明 |
|---|---|---|
| `--model` | small | small／medium／large-v3，越大越準、越慢 |
| `--prompt` | 以下是繁體中文的內容。 | 可以放常出現的人名和術語，提高辨識率 |
| `--glossary` | 無 | 每行一組「錯字=>正確」，見 `glossary.example.txt` |
| `--max-chars` | 12 | 每句字幕最多幾個字 |
| `--pad` / `--min-gap` | 0.12 / 0.35 | 剪停頓的靈敏度；剪得太碎就把 `--min-gap` 調大 |
| `--margin-v` | 520 | 字幕離底部多遠，預設會避開 Shorts 的按鈕和說明文字 |
| `--overlay` | 無 | 1080x1920 透明 PNG |
| `--retranscribe` | 關 | 捨棄已有的 subs.json，重新辨識 |

## Canva 品牌框怎麼做

1. 在 Canva 開一個 1080x1920 的設計，頂部放標題列，底部放 CTA，**中間留空**。
2. 刪除背景（或把背景設成透明）。
3. 下載：PNG ＋ 勾選「透明背景」（需要 Canva Pro）。
4. 另存成 `frame.png`，跑腳本時加上 `--overlay frame.png`。

標題列每集都不一樣的話，在 Canva 複製一頁、改標題、再匯出一張 PNG 就好。

## 輸出檔

| 檔案 | 用途 |
|---|---|
| `out/片段_shorts.mp4` | 成品，可以直接上傳 |
| `out/片段.srt` | 字幕檔，也可以上傳到 YouTube 當作 CC 字幕 |
| `out/片段.subs.json` | 辨識結果，可以修改 `cues[].text`，再跑一次就會套用 |
