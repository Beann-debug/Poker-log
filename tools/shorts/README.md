# 長片 → Shorts 混合流程（Canva ＋ 字幕腳本）

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
