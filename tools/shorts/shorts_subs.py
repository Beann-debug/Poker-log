#!/usr/bin/env python3
"""把 Canva Highlights 匯出的片段，自動做成帶繁中字幕的 9:16 Shorts。

流程：
  1. 語音辨識（faster-whisper，含逐字時間戳）→ 轉繁中台灣用語（OpenCC s2twp）＋自訂詞表
  2. 產生 <片名>.subs.json（可手動修字）與 <片名>.srt
  3. 依逐字時間戳剪掉停頓
  4. 轉 1080x1920：原片置中，上下用模糊背景補滿
  5. 燒入大字幕；可疊上 Canva 匯出的透明 PNG 品牌框

用法：
  python3 shorts_subs.py clip.mp4                       # 辨識＋輸出
  python3 shorts_subs.py clip.mp4                       # 改完 subs.json 後再跑一次：沿用修改，不重新辨識
  python3 shorts_subs.py clip.mp4 --overlay frame.png --glossary glossary.txt
  python3 shorts_subs.py clip.mp4 --retranscribe        # 捨棄 subs.json 重新辨識
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

W, H, FPS = 1080, 1920, 30
PUNCT = "，。！？、；：,.!?;:…「」『』（）()\"' "


def transcribe(clip, model_name, language, prompt):
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="auto", compute_type="int8")
    segments, _ = model.transcribe(
        str(clip),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt=prompt,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"start": round(w.start, 3), "end": round(w.end, 3), "text": text})
    return words


def load_glossary(path):
    pairs = []
    if path:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=>" in line:
                wrong, right = line.split("=>", 1)
                pairs.append((wrong.strip(), right.strip()))
    return pairs


def to_traditional(text, glossary):
    import opencc

    text = opencc.OpenCC("s2twp").convert(text)
    for wrong, right in glossary:
        text = text.replace(wrong, right)
    return text


def build_cues(words, max_chars, max_gap):
    """把逐字結果切成字幕句：字數到上限、停頓過長或遇到標點就換句。"""
    cues, cur = [], []

    def flush():
        if cur:
            text = "".join(w["text"] for w in cur).strip(PUNCT)
            if text:
                cues.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": text})
            cur.clear()

    for w in words:
        length = len("".join(x["text"] for x in cur))
        if cur and (w["start"] - cur[-1]["end"] > max_gap or length + len(w["text"]) > max_chars):
            flush()
        cur.append(w)
        if w["text"][-1] in PUNCT:
            flush()
    flush()
    return cues


def keep_intervals(words, duration, pad, min_gap):
    """保留有講話的區段（前後各留 pad 秒），中間停頓短於 min_gap 就不剪。"""
    if not words:
        return [(0.0, duration)]
    spans = [(max(0.0, w["start"] - pad), min(duration, w["end"] + pad)) for w in words]
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a - merged[-1][1] <= min_gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in merged]


def remap(t, intervals):
    """原片時間 → 剪完後的時間。落在被剪掉的區段時，對齊到下一段開頭。"""
    offset = 0.0
    for a, b in intervals:
        if t < a:
            return offset
        if t <= b:
            return offset + (t - a)
        offset += b - a
    return offset


def fmt_ts(t, sep):
    if sep == ",":  # SRT：毫秒
        ms = int(round(max(t, 0.0) * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    cs = int(round(max(t, 0.0) * 100))  # ASS：百分之一秒
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def write_srt(cues, path):
    lines = []
    for i, c in enumerate(cues, 1):
        lines += [str(i), f"{fmt_ts(c['start'], ',')} --> {fmt_ts(c['end'], ',')}", c["text"], ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def write_ass(cues, path, font, size, margin_v):
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,2,0,1,7,2,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [
        f"Dialogue: 0,{fmt_ts(c['start'], '.')},{fmt_ts(c['end'], '.')},Default,,0,0,0,,{c['text']}"
        for c in cues
    ]
    Path(path).write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def probe_duration(clip):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def has_audio(clip):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(clip)],
        capture_output=True, text=True, check=True,
    )
    return bool(out.stdout.strip())


def filter_escape(path):
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def render(clip, out, intervals, ass_path, overlay, fonts_dir):
    sel = "+".join(f"between(t,{a},{b})" for a, b in intervals)
    ass_opt = f"ass=filename='{filter_escape(ass_path)}'"
    if fonts_dir:
        ass_opt += f":fontsdir='{filter_escape(fonts_dir)}'"
    graph = [
        f"[0:v]fps={FPS},select='{sel}',setpts=N/{FPS}/TB,split[bgsrc][fgsrc]",
        f"[bgsrc]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:2,eq=brightness=-0.15[bg]",
        f"[fgsrc]scale={W}:{H}:force_original_aspect_ratio=decrease[fg]",
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]",
    ]
    inputs = ["-i", str(clip)]
    if overlay:
        inputs += ["-i", str(overlay)]
        graph.append(f"[1:v]scale={W}:{H}[ov]")
        graph.append("[base][ov]overlay=0:0[framed]")
        graph.append(f"[framed]{ass_opt}[v]")
    else:
        graph.append(f"[base]{ass_opt}[v]")
    maps = ["-map", "[v]"]
    if has_audio(clip):
        graph.append(f"[0:a]aselect='{sel}',asetpts=N/SR/TB[a]")
        maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph), *maps,
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)


def transcribe_traditional(clip, a):
    """辨識並轉成繁中台灣用語，套用詞表。"""
    print(f"辨識中（模型 {a.model}）…")
    glossary = load_glossary(a.glossary)
    try:
        words = transcribe(clip, a.model, a.language, a.prompt)
    except ImportError:
        sys.exit("缺少套件，請先執行：pip install -r requirements.txt")
    except Exception as e:  # 多半是第一次下載模型時連不上 Hugging Face
        sys.exit(f"語音辨識失敗：{e}\n第一次執行需要連網下載模型，請確認網路可連到 huggingface.co。")
    for w in words:
        w["text"] = to_traditional(w["text"], glossary)
    return words


def add_common_args(p):
    """辨識與輸出參數（highlights.py 也共用）。"""
    p.add_argument("--model", default="small", help="Whisper 模型：small / medium / large-v3（越大越準越慢）")
    p.add_argument("--language", default="zh")
    p.add_argument("--prompt", default="以下是繁體中文的內容。", help="給辨識模型的提示，可放常出現的專有名詞")
    p.add_argument("--glossary", type=Path, help="詞表檔，每行「錯字=>正確」")
    p.add_argument("--retranscribe", action="store_true", help="忽略既有 subs.json，重新辨識")
    p.add_argument("--no-cut", action="store_true", help="不剪停頓")
    p.add_argument("--pad", type=float, default=0.12, help="每段講話前後保留秒數")
    p.add_argument("--min-gap", type=float, default=0.35, help="短於此秒數的停頓不剪")
    p.add_argument("--max-chars", type=int, default=12, help="每句字幕最多字數")
    p.add_argument("--font", default="Noto Sans TC")
    p.add_argument("--fonts-dir", type=Path, help="字型資料夾（字型沒安裝在系統時使用）")
    p.add_argument("--font-size", type=int, default=84)
    p.add_argument("--margin-v", type=int, default=520, help="字幕離底部的距離（避開 Shorts 介面）")
    p.add_argument("--overlay", type=Path, help="Canva 匯出的 1080x1920 透明 PNG 品牌框")


def make_short(clip, out_dir, data, duration, a):
    """依 subs.json 的內容剪停頓、轉 9:16、燒字幕，輸出 <片名>_shorts.mp4。"""
    intervals = [(0.0, duration)] if a.no_cut else keep_intervals(data["words"], duration, a.pad, a.min_gap)
    cues = [
        {"start": remap(c["start"], intervals), "end": remap(c["end"], intervals), "text": c["text"]}
        for c in data["cues"]
    ]
    cues = [c for c in cues if c["end"] > c["start"]]

    write_srt(cues, out_dir / f"{clip.stem}.srt")
    out_mp4 = out_dir / f"{clip.stem}_shorts.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        ass_path = Path(tmp) / "subs.ass"
        write_ass(cues, ass_path, a.font, a.font_size, a.margin_v)
        render(clip, out_mp4, intervals, ass_path, a.overlay, a.fonts_dir)

    kept = sum(b - a_ for a_, b in intervals)
    print(f"完成：{out_mp4}")
    print(f"原長 {duration:.1f}s → 剪後 {kept:.1f}s，字幕 {len(cues)} 句")
    if kept > 60:
        print("提醒：超過 60 秒，請挑短一點的片段。")
    return out_mp4


def main():
    p = argparse.ArgumentParser(description="Canva 片段 → 9:16 繁中字幕 Shorts")
    p.add_argument("clip", type=Path)
    p.add_argument("--out-dir", type=Path, default=None, help="輸出資料夾（預設與片段同層的 out/）")
    add_common_args(p)
    a = p.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("找不到 ffmpeg，請先安裝。")
    clip = a.clip.resolve()
    out_dir = (a.out_dir or clip.parent / "out").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    subs_json = out_dir / f"{clip.stem}.subs.json"
    duration = probe_duration(clip)

    if subs_json.exists() and not a.retranscribe:
        data = json.loads(subs_json.read_text(encoding="utf-8"))
        print(f"沿用 {subs_json.name}（要重新辨識請加 --retranscribe）")
    else:
        words = transcribe_traditional(clip, a)
        cues = build_cues(words, a.max_chars, max_gap=0.45)
        data = {"words": words, "cues": cues}
        subs_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已寫出 {subs_json.name}：可直接修改 cues 的 text，再跑一次就會套用")

    make_short(clip, out_dir, data, duration, a)


if __name__ == "__main__":
    main()
