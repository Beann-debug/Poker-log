#!/usr/bin/env python3
"""長片 → Gemini 自動挑精華 → 多支帶繁中字幕的 9:16 Shorts。

流程：
  1. 整支長片語音辨識（快取在 out/<片名>.transcript.json，只跑一次）
  2. 把帶時間戳的逐字稿交給 Gemini，挑出 N 段可以獨立觀看的精華
     → 寫出 out/<片名>.highlights.json（可手動調整起訖時間、刪掉不要的段落）
  3. 每段切出來，直接沿用長片的辨識結果產生字幕（不用重新辨識），做成 Shorts
  4. 標題、開場鉤子寫進 out/<片名>.titles.md，上傳時可直接複製

用法：
  export GEMINI_API_KEY=你的key
  python3 highlights.py long.mp4 --count 3 --overlay frame.png
  python3 highlights.py long.mp4              # 改完 highlights.json 後再跑：沿用你的選段
  python3 highlights.py long.mp4 --repick     # 讓 Gemini 重新挑
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shorts_subs import (  # noqa: E402
    add_common_args,
    build_cues,
    make_short,
    probe_duration,
    transcribe_traditional,
)

API_BASE = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")

PROMPT = """你是短影音剪輯師。以下是一支長影片的逐字稿，每行格式為 [開始秒數-結束秒數] 內容。

請挑出 {count} 段最適合做成 YouTube Shorts 的片段，條件：
- 每段長度 {min_len}–{max_len} 秒
- 開頭 3 秒內就要有吸引人的鉤子（問題、反常識、具體數字、強烈主張）
- 內容能獨立看懂，不需要前後文；有完整的結論或重點
- 起訖時間必須對齊逐字稿某一行的開始與結束，不要從句子中間切
- 各段不可重疊，挑最精彩的，不要平均分配
{extra}
針對每段提供：繁體中文標題（20 字內，適合 Shorts）、開場鉤子句、挑選理由、1–10 分的爆紅潛力分數。

逐字稿：
{transcript}
"""

SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "start": {"type": "NUMBER"},
            "end": {"type": "NUMBER"},
            "title": {"type": "STRING"},
            "hook": {"type": "STRING"},
            "reason": {"type": "STRING"},
            "score": {"type": "NUMBER"},
        },
        "required": ["start", "end", "title", "hook", "reason", "score"],
    },
}


def call_gemini(prompt, model, api_key):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
            "temperature": 0.4,
        },
    }
    req = urllib.request.Request(
        f"{API_BASE}/models/{model}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"Gemini API 錯誤 {e.code}：{e.read().decode('utf-8', 'replace')[:500]}")
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError):
        sys.exit(f"Gemini 回傳格式無法解析：{json.dumps(data, ensure_ascii=False)[:500]}")


def snap(picks, lines, duration, min_len, max_len):
    """把 Gemini 給的時間對齊到逐字稿句子邊界，並剔除長度不合、互相重疊的段落。"""
    starts = [l["start"] for l in lines]
    ends = [l["end"] for l in lines]
    result = []
    for p in sorted(picks, key=lambda x: -x.get("score", 0)):
        s = min(starts, key=lambda t: abs(t - p["start"]))
        e = min((t for t in ends if t > s), key=lambda t: abs(t - p["end"]), default=None)
        if e is None:
            continue
        s, e = max(0.0, s - 0.15), min(duration, e + 0.3)
        if not (min_len * 0.8 <= e - s <= max_len * 1.1):
            print(f"略過（長度 {e - s:.0f}s 不合）：{p['title']}")
            continue
        if any(s < r["end"] and e > r["start"] for r in result):
            print(f"略過（與其他段重疊）：{p['title']}")
            continue
        result.append({**p, "start": round(s, 2), "end": round(e, 2)})
    return sorted(result, key=lambda x: x["start"])


def cut_clip(src, start, end, out):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{start}", "-to", f"{end}", "-i", str(src),
         "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(out)],
        check=True,
    )


def clip_subs(words, start, end, max_chars):
    """從長片的逐字結果取出這一段，時間改成從 0 起算。"""
    ws = [
        {"start": round(w["start"] - start, 3), "end": round(w["end"] - start, 3), "text": w["text"]}
        for w in words
        if w["start"] >= start and w["end"] <= end
    ]
    return {"words": ws, "cues": build_cues(ws, max_chars, max_gap=0.45)}


def fmt_time(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def main():
    p = argparse.ArgumentParser(description="長片 → Gemini 挑精華 → 繁中字幕 Shorts")
    p.add_argument("video", type=Path)
    p.add_argument("--out-dir", type=Path, default=None, help="輸出資料夾（預設與影片同層的 out/）")
    p.add_argument("--count", type=int, default=3, help="要挑幾段")
    p.add_argument("--min-len", type=int, default=20, help="每段最短秒數")
    p.add_argument("--max-len", type=int, default=55, help="每段最長秒數（剪停頓後通常會再短一些）")
    p.add_argument("--gemini-model", default="gemini-2.5-flash")
    p.add_argument("--topic", default="", help="補充說明給 Gemini，例如目標觀眾、想強調的主題")
    p.add_argument("--repick", action="store_true", help="忽略既有 highlights.json，讓 Gemini 重新挑")
    p.add_argument("--select-only", action="store_true", help="只挑段落、不產出影片（先檢查選段再決定）")
    add_common_args(p)
    a = p.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("找不到 ffmpeg，請先安裝。")
    video = a.video.resolve()
    out_dir = (a.out_dir or video.parent / "out").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(video)

    # 1. 整支辨識（快取）
    transcript_json = out_dir / f"{video.stem}.transcript.json"
    if transcript_json.exists() and not a.retranscribe:
        words = json.loads(transcript_json.read_text(encoding="utf-8"))["words"]
        print(f"沿用 {transcript_json.name}")
    else:
        words = transcribe_traditional(video, a)
        transcript_json.write_text(json.dumps({"words": words}, ensure_ascii=False, indent=1), encoding="utf-8")
    if not words:
        sys.exit("辨識不到任何語音。")
    lines = build_cues(words, max_chars=40, max_gap=0.8)

    # 2. Gemini 選段（快取，可手動修改）
    plan_json = out_dir / f"{video.stem}.highlights.json"
    if plan_json.exists() and not a.repick:
        picks = json.loads(plan_json.read_text(encoding="utf-8"))
        print(f"沿用 {plan_json.name}（共 {len(picks)} 段；要重挑請加 --repick）")
    else:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            sys.exit("請先設定環境變數 GEMINI_API_KEY。")
        transcript = "\n".join(f"[{l['start']:.1f}-{l['end']:.1f}] {l['text']}" for l in lines)
        extra = f"- 補充說明：{a.topic}\n" if a.topic else ""
        prompt = PROMPT.format(count=a.count, min_len=a.min_len, max_len=a.max_len,
                               extra=extra, transcript=transcript)
        print(f"Gemini（{a.gemini_model}）挑選中…")
        raw = call_gemini(prompt, a.gemini_model, api_key)
        picks = snap(raw, lines, duration, a.min_len, a.max_len)[: a.count]
        if not picks:
            sys.exit("Gemini 沒有挑出符合條件的段落，可調整 --min-len / --max-len 或加 --topic 後重試。")
        plan_json.write_text(json.dumps(picks, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已寫出 {plan_json.name}：可修改起訖時間或刪掉不要的段落，再跑一次就會套用")

    for i, pk in enumerate(picks, 1):
        print(f"  {i}. [{fmt_time(pk['start'])}–{fmt_time(pk['end'])}] {pk['title']}（{pk.get('score', '?')} 分）")
    if a.select_only:
        return

    # 3. 每段做成 Shorts
    clips_dir = out_dir / f"{video.stem}_clips"
    clips_dir.mkdir(exist_ok=True)
    notes = [f"# {video.stem} 精華片段\n"]
    for i, pk in enumerate(picks, 1):
        clip = clips_dir / f"{video.stem}_{i:02d}.mp4"
        print(f"\n[{i}/{len(picks)}] {pk['title']}")
        cut_clip(video, pk["start"], pk["end"], clip)
        data = clip_subs(words, pk["start"], pk["end"], a.max_chars)
        (clips_dir / f"{clip.stem}.subs.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        out_mp4 = make_short(clip, clips_dir, data, probe_duration(clip), a)
        notes.append(
            f"## {i}. {pk['title']}\n\n"
            f"- 檔案：`{out_mp4.name}`\n- 原片位置：{fmt_time(pk['start'])}–{fmt_time(pk['end'])}\n"
            f"- 開場鉤子：{pk['hook']}\n- 挑選理由：{pk['reason']}\n- 爆紅潛力：{pk.get('score', '?')}/10\n"
        )
    titles = out_dir / f"{video.stem}.titles.md"
    titles.write_text("\n".join(notes), encoding="utf-8")
    print(f"\n全部完成，標題與說明在 {titles}")
    print(f"字幕有錯字：改 {clips_dir.name}/片名.subs.json 後執行\n  python3 shorts_subs.py {clips_dir}/片名.mp4 --out-dir {clips_dir}")


if __name__ == "__main__":
    main()
