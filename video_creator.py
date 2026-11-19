#!/usr/bin/env python3
"""
video_creator.py — script → voice → visuals → captions → FFmpeg → MP4
=====================================================================

Poora pipeline ek file me (6GB VRAM / 16GB RAM friendly — CPU-only, GPU load zero).

Voice ladder (auto fallback):
    voicestudio (localhost:3900, optional app)
      → edge-tts  (free neural, internet)
        → piper   (offline, model chahiye)
          → silent stub (0.9s placeholder — pipeline test ke liye)

Visual modes:
    cards      — PIL generated cards (default, zero API)
    stock      — Pexels / Pixabay free stock footage (API key chahiye)
    higgsfield — cloud generation (HIGGSFIELD_API_KEY, paid)

Usage:
    python video_creator.py "aaj ka topic" --lang hi
    python video_creator.py --doctor
    python video_creator.py --audit
    python video_creator.py --dry-run "topic"
    python video_creator.py "topic" --scenes 5 --music --size vertical

Exit codes: 0 ok | 2 dependency/config error | 3 render failure
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

VERSION = "1.1.0"
TOOL = "video_creator.py"

# ---------------------------------------------------------------------------
# Constants (artifacts reverse-engineered from repo output — faithful to v1)
# ---------------------------------------------------------------------------
SCENE_PAD_S = 0.8          # voice ke baad breathing room
MIN_SCENE_S = 2.2          # scene minimum duration (stub report me 2.2s tha)
STUB_VOICE_S = 0.9         # silent-stub voice duration (voice_1.wav = 0.9s @22050 mono)
CAPTION_TAIL_S = 0.6       # last word ke baad caption tail
FPS = 25

SIZES = {
    "horizontal": (1920, 1080),
    "vertical":   (1080, 1920),
    "square":     (1080, 1080),
}

ENGINES = ("auto", "voicestudio", "edge", "piper", "silent")
VISUALS = ("cards", "stock", "higgsfield")
LANGS = ("hi", "en")

VOICE_STUDIO_PORT = int(os.environ.get("VOICESTUDIO_PORT", "3900"))
VOICE_STUDIO_URL = os.environ.get("VOICESTUDIO_URL", f"http://127.0.0.1:{VOICE_STUDIO_PORT}")

HARDWARE_NOTE = "6GB VRAM / 16GB RAM safe — heavy generation cloud/app me (optional)"

EDGE_VOICES = {
    "hi": "hi-IN-SwaraNeural",
    "en": "en-IN-NeerjaNeural",
}


# ===========================================================================
# 0) LOGGING HELPER (CLI + web app dono use karte hain)
# ===========================================================================
class Logger:
    # pipe broken hua ya nahi (interpreter-shutdown flush ke liye global flag)
    broken_pipe = False

    def __init__(self, sink=None):
        self._sink_fn = sink
        self._stub_warned = False
        self._broken = False

    def __call__(self, line: str):
        if self._broken:
            return
        try:
            if self._sink_fn is not None:
                self._sink_fn(line)
            else:
                print(line, flush=True)
        except OSError:
            # pipe close ho gaya (jaise `... | head`) — silently chup, crash nahi
            self._broken = True
            Logger.broken_pipe = True


def _http_json(url: str, timeout: float = 3.0, method: str = "GET", payload: dict | None = None):
    """Small urllib wrapper. Returns (status, parsed_or_raw). Never raises on network error."""
    try:
        data = None
        headers = {"User-Agent": f"{TOOL}/{VERSION}"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype:
                try:
                    return resp.status, json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    return resp.status, None
            return resp.status, raw
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return None, None


# ===========================================================================
# 1) FFMPEG DISCOVERY  (system → env → imageio-ffmpeg fallback → clear error)
# ===========================================================================
def find_ffmpeg(logger=None) -> str | None:
    p = shutil.which("ffmpeg")
    if p:
        return p
    env = os.environ.get("FFMPEG")
    if env and Path(env).exists():
        return env
    try:
        import imageio_ffmpeg  # type: ignore

        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p:
            if logger:
                logger("[i] system ffmpeg nahi mila — imageio-ffmpeg ke bundled ffmpeg ka use hoga")
            return p
    except ImportError:
        pass
    return None


def find_ffprobe() -> str | None:
    p = shutil.which("ffprobe")
    return p


def ffmpeg_has_filter(ffmpeg: str, name: str) -> bool:
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=20,
        )
        return name in out.stdout
    except Exception:
        return False


def probe_duration(ffmpeg: str, path: Path) -> float | None:
    ffprobe = find_ffprobe()
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            return float(out.stdout.strip())
        except Exception:
            pass
    # fallback: parse ffmpeg -i stderr
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                             capture_output=True, text=True, timeout=30)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", out.stderr)
        if m:
            h, mn, s = m.groups()
            return int(h) * 3600 + int(mn) * 60 + float(s)
    except Exception:
        pass
    return None


# ===========================================================================
# 2) SCRIPT GENERATOR (Hinglish / English templates + --notes grounding)
# ===========================================================================
_HI_ANGLES = [
    "basics clear karo, baaki easy ho jaata hai.",
    "small steps daily lo, results bade dikhenge.",
    "galtiyan sab karte hain — bas unse seekho.",
    "sahi tools use karo, ghante bach jaate hain.",
    "practice hi asli shortcut hai.",
    "consistency dikhayegi, luck nahi.",
]

_EN_ANGLES = [
    "master the basics and the rest gets easy.",
    "small daily steps beat big rare efforts.",
    "mistakes are fine — learn from them fast.",
    "the right tools save you hours.",
    "practice is the real shortcut.",
    "consistency wins, not luck.",
]


def slugify(topic: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")
    return (s or "video")[:max_len]


def build_script(topic: str, lang: str, n_scenes: int, notes_dir: Path | None,
                 logger=None) -> list[dict]:
    """Return [{title, text}] with exactly n_scenes entries (min 3: hook/outro)."""
    n_scenes = max(3, min(6, n_scenes))
    lang = lang if lang in LANGS else "hi"

    hook = (f"Aaj ka topic hai: {topic}. Last tak dekho — ye cheez kaam ki hai."
            if lang == "hi" else
            f"Today's topic: {topic}. Watch till the end — this one is useful.")
    outro = (f"To ye thi {topic} ki quick guide. Pasand aayi to share karo — "
             f"milte hain next video me!"
             if lang == "hi" else
             f"That was the quick guide on {topic}. If it helped, share it — "
             f"see you in the next video!")

    angles = _HI_ANGLES if lang == "hi" else _EN_ANGLES
    n_points = n_scenes - 2

    # --notes grounding: keyword-scored sentences from knowledge folder
    grounded: list[str] = []
    if notes_dir is not None:
        grounded = _ground_from_notes(notes_dir, topic, n_points, logger)
        if not grounded and logger:
            logger("[!] notes folder me topic se matching content nahi mila — generic script")

    points: list[str] = []
    for i in range(n_points):
        if i < len(grounded):
            points.append(grounded[i])
        elif lang == "hi":
            points.append(f"{topic} ko samajhna mushkil nahi hai — {angles[i % len(angles)]}")
        else:
            points.append(f"{topic} is not hard — {angles[i % len(angles)]}")

    titles = ["Hook"] + [f"Point {i + 1}" for i in range(n_points)] + ["Outro"]
    texts = [hook] + points + [outro]
    return [{"title": t, "text": x} for t, x in zip(titles, texts)]


def _ground_from_notes(notes_dir: Path, topic: str, want: int, logger=None) -> list[str]:
    if not notes_dir.exists():
        if logger:
            logger(f"[!] notes folder nahi mila: {notes_dir}")
        return []
    files = [p for p in sorted(notes_dir.rglob("*")) if p.suffix.lower() in (".txt", ".md")]
    keywords = {w for w in re.findall(r"[a-zA-Z]{3,}", topic.lower())}
    scored: list[tuple[float, str]] = []
    for p in files[:50]:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
            sent = sent.strip()
            if not (20 <= len(sent) <= 220):
                continue
            words = set(re.findall(r"[a-zA-Z]{3,}", sent.lower()))
            overlap = len(words & keywords)
            if overlap:
                scored.append((overlap / (1 + math.log(1 + len(words))), sent))
    scored.sort(reverse=True, key=lambda x: x[0])
    if logger and scored:
        logger(f"[i] notes grounding: {len(scored)} matching sentences, top {want} use honge "
               f"({len(files)} files)")
    return [s for _, s in scored[:want]]


# ===========================================================================
# 3) VOICE ENGINES — ladder: voicestudio → edge → piper → silent
# ===========================================================================
def _write_stub_wav(path: Path, dur: float = STUB_VOICE_S) -> float:
    """Silent placeholder — repo artifact voice_1.wav jaisa: 0.9s @22050 mono 16bit."""
    rate = 22050
    n = int(rate * dur)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * n)
    return dur


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return round(w.getnframes() / w.getframerate(), 3)
    except Exception:
        return 0.0


def voicestudio_available() -> bool:
    for path in ("/health", "/api/health", "/"):
        status, _ = _http_json(VOICE_STUDIO_URL + path, timeout=2.0)
        if status == 200:
            return True
    return False


def tts_voicestudio(text: str, lang: str, out: Path) -> tuple[float, list[dict]]:
    """VoiceStudio local app (port 3900). Returns (duration_s, words[]) on success, raises otherwise."""
    status, raw = _http_json(VOICE_STUDIO_URL + "/tts", timeout=60, method="POST",
                             payload={"text": text, "lang": lang, "voice": "auto",
                                      "format": "wav"})
    if status != 200 or not isinstance(raw, (bytes, bytearray)) or len(raw) < 100:
        raise RuntimeError(f"voicestudio /tts failed (status={status})")
    out.write_bytes(raw)
    dur = _wav_duration(out)
    if dur <= 0:
        raise RuntimeError("voicestudio returned non-wav audio")
    return dur, []  # word boundaries not exposed by app


def tts_edge(text: str, lang: str, out: Path, voice: str | None = None) -> tuple[float, list[dict]]:
    """edge-tts with word-boundary capture (SRT captions isi se bante hain)."""
    try:
        import edge_tts  # type: ignore
    except ImportError as e:
        raise RuntimeError("edge-tts installed nahi hai (pip install edge-tts)") from e

    voice = voice or EDGE_VOICES.get(lang, EDGE_VOICES["hi"])

    async def _run():
        words: list[dict] = []
        comm = edge_tts.Communicate(text, voice, rate="-5%")
        with open(out, "wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    words.append({
                        "start": chunk["offset"] / 1e7,   # 100ns ticks → s
                        "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                        "word": chunk["text"],
                    })
        return words

    async def _guarded():
        return await asyncio.wait_for(_run(), timeout=30)

    try:
        words = asyncio.run(_guarded())
    except (asyncio.TimeoutError, Exception) as e:
        raise RuntimeError(f"edge-tts failed: {e}") from e

    dur = _wav_duration(out)
    if dur <= 0:
        raise RuntimeError("edge-tts produced no audio")
    return dur, words


def tts_piper(text: str, out: Path, model: Path | None) -> tuple[float, list[dict]]:
    exe = shutil.which("piper")
    if not exe:
        raise RuntimeError("piper CLI nahi mila")
    if not model or not model.exists():
        raise RuntimeError(f"piper model nahi mila: {model}")
    try:
        subprocess.run([exe, "--model", str(model), "--output_file", str(out)],
                       input=text.encode("utf-8"), capture_output=True, timeout=60, check=True)
    except (subprocess.SubprocessError, OSError) as e:
        raise RuntimeError(f"piper failed: {e}") from e
    dur = _wav_duration(out)
    if dur <= 0:
        raise RuntimeError("piper produced no audio")
    return dur, []


def synth_voice(scene: dict, lang: str, engine: str, out: Path,
                piper_model: Path | None, logger, voice: str | None = None) -> tuple[str, float, list[dict]]:
    """Ladder resolver. Returns (engine_used, duration, words)."""
    ladder = {
        "auto":        ["voicestudio", "edge", "piper", "silent"],
        "voicestudio": ["voicestudio", "silent"],
        "edge":        ["edge", "silent"],
        "piper":       ["piper", "silent"],
        "silent":      ["silent"],
    }[engine]

    for step in ladder:
        try:
            if step == "voicestudio":
                if not voicestudio_available():
                    raise RuntimeError("VoiceStudio app nahi chal raha (port 3900)")
                dur, words = tts_voicestudio(scene["text"], lang, out)
            elif step == "edge":
                dur, words = tts_edge(scene["text"], lang, out, voice=voice)
            elif step == "piper":
                dur, words = tts_piper(scene["text"], out, piper_model)
            else:  # silent stub
                dur, words = _write_stub_wav(out), []
                if not logger._stub_warned:  # noqa: SLF001 — ek hi baar warn karo
                    logger("[!] koi voice engine nahi mila — silent stub voice use ho rahi hai "
                           "(video to banegi, voice baad me)")
                    logger._stub_warned = True  # noqa: SLF001
            return step, dur, words
        except RuntimeError as e:
            logger(f"[!] voice:{step} → {e}")
    return "silent", _write_stub_wav(out), []


# ===========================================================================
# 4) VISUALS — cards (PIL) / stock (Pexels|Pixabay) / higgsfield (cloud)
# ===========================================================================
_PALETTES = [
    ((15, 23, 42), (37, 99, 235)),
    ((20, 30, 48), (5, 150, 105)),
    ((24, 24, 40), (147, 51, 234)),
    ((26, 18, 37), (219, 39, 119)),
    ((18, 26, 35), (234, 88, 12)),
    ((10, 30, 40), (14, 165, 233)),
]

_BRAND = "VAANIREEL"


def make_card(idx: int, total: int, title: str, text: str, size: tuple[int, int],
              out: Path, topic: str) -> Path:
    """PIL card — gradient bg + accent bar + scene index + title/text + brand."""
    from PIL import Image, ImageDraw, ImageFont  # local import (optional dep)

    w, h = size
    bg, accent = _PALETTES[(idx - 1) % len(_PALETTES)]
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)

    # vertical gradient
    for y in range(h):
        t = y / h
        c = tuple(int(bg[i] + (accent[i] - bg[i]) * t * 0.35) for i in range(3))
        d.line([(0, y), (w, y)], fill=c)

    font_dir = _font_dir()
    f_big = _load_font(font_dir, int(h * 0.085))
    f_med = _load_font(font_dir, int(h * 0.045))
    f_small = _load_font(font_dir, int(h * 0.026))

    # accent bar
    bar_w = int(w * 0.12)
    d.rounded_rectangle([w // 2 - bar_w // 2, int(h * 0.14),
                         w // 2 + bar_w // 2, int(h * 0.14) + max(8, h // 200)],
                        radius=max(4, h // 400), fill=accent)

    # scene index
    d.text((w // 2, int(h * 0.22)), f"{idx:02d} / {total:02d}",
           font=f_small, fill=(148, 163, 184), anchor="mm")

    # title
    d.text((w // 2, int(h * 0.34)), title, font=f_big, fill=(255, 255, 255), anchor="mm")

    # text wrapped
    y = int(h * 0.5)
    for line in _wrap(text, f_med, int(w * 0.78), d):
        d.text((w // 2, y), line, font=f_med, fill=(226, 232, 240), anchor="mm")
        y += int(h * 0.062)

    # brand
    d.text((w // 2, int(h * 0.9)), _BRAND, font=f_small, fill=(100, 116, 139), anchor="mm")
    d.text((w // 2, int(h * 0.945)), topic[:60], font=f_small, fill=(71, 85, 105), anchor="mm")

    img.save(out, "PNG")
    return out


def _font_dir() -> Path | None:
    for cand in (
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/dejavu"),
        Path("C:/Windows/Fonts"),
        Path("/System/Library/Fonts"),
        Path.home() / ".fonts",
    ):
        if cand.exists():
            return cand
    return None


def _load_font(font_dir: Path | None, size: int):
    from PIL import ImageFont
    names = ["DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arialbd.ttf", "arial.ttf",
             "Helvetica.ttc"]
    if font_dir:
        for n in names:
            p = font_dir / n
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except OSError:
                    continue
    return ImageFont.load_default()


def _wrap(text: str, font, max_w: int, d) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        trial = f"{cur} {w_}".strip()
        if d.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


def fetch_stock_clip(query: str, idx: int, dur: float, out: Path, logger) -> Path | None:
    """Pexels → Pixabay fallback. Returns video path or None (→ card fallback + tip)."""
    pexels_key = os.environ.get("PEXELS_API_KEY")
    pixabay_key = os.environ.get("PIXABAY_API_KEY")
    headers = {"User-Agent": f"{TOOL}/{VERSION}"}
    video_url = None

    if pexels_key:
        try:
            req = urllib.request.Request(
                "https://api.pexels.com/videos/search?query="
                + urllib.parse.quote(query) + "&per_page=5",
                headers={**headers, "Authorization": pexels_key})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            vids = data.get("videos") or []
            if vids:
                files = sorted(vids[idx % len(vids)].get("video_files", []),
                               key=lambda f: f.get("width") or 0)
                hd = [f for f in files if (f.get("width") or 0) >= 1280]
                video_url = (hd or files or [{}])[-1].get("link")
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            logger(f"[!] pexels search failed: {e}")
    elif pixabay_key:
        try:
            req = urllib.request.Request(
                "https://pixabay.com/api/videos/?key=" + urllib.parse.quote(pixabay_key)
                + "&q=" + urllib.parse.quote(query) + "&per_page=5", headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            hits = data.get("hits") or []
            if hits:
                vids = hits[idx % len(hits)].get("videos", {})
                video_url = (vids.get("medium") or vids.get("small") or {}).get("url")
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            logger(f"[!] pixabay search failed: {e}")
    else:
        logger("[!] stock keys nahi mile (PEXELS_API_KEY / PIXABAY_API_KEY) — card fallback")
        return None

    if not video_url:
        logger("[!] stock clip nahi mila — card fallback")
        return None
    try:
        req = urllib.request.Request(video_url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r, open(out, "wb") as f:
            shutil.copyfileobj(r, f)
        if out.exists() and out.stat().st_size > 10_000:
            return out
    except (urllib.error.URLError, OSError) as e:
        logger(f"[!] stock download failed: {e}")
    return None


# ===========================================================================
# 5) MUSIC (numpy synth ambient pad — koi asset nahi chahiye)
# ===========================================================================
def synth_music(path: Path, dur: float, logger) -> Path | None:
    try:
        import numpy as np
    except ImportError:
        logger("[!] numpy nahi hai — music skip (pip install numpy)")
        return None
    rate = 44100
    total = int(rate * max(dur, 4))
    t = np.arange(total) / rate
    # chord progression: Am F C G (Hz bases)
    chords = [(220.0, 261.63, 329.63), (174.61, 220.0, 261.63),
              (261.63, 329.63, 392.0), (196.0, 246.94, 293.66)]
    seg = total // len(chords)
    audio = np.zeros(total, dtype=np.float64)
    for ci, chord in enumerate(chords):
        s0, s1 = ci * seg, min((ci + 1) * seg + rate, total)  # 1s cross-blend
        n = s1 - s0
        tt = np.arange(n) / rate
        env = np.minimum(tt / 1.5, 1.0) * np.minimum((n / rate - tt) / 1.5, 1.0).clip(0, 1)
        wave_ = sum(np.sin(2 * np.pi * f * tt) for f in chord)
        audio[s0:s1] += wave_ * env / len(chords)
    # slow LFO + soft noise floor
    audio *= (0.8 + 0.2 * np.sin(2 * np.pi * 0.1 * t))
    audio += 0.01 * np.sin(2 * np.pi * 55 * t)
    peak = np.abs(audio).max() or 1.0
    audio = audio / peak * 0.5
    # gentle fade in/out
    fade = int(rate * 1.0)
    audio[:fade] *= np.linspace(0, 1, fade)
    audio[-fade:] *= np.linspace(1, 0, fade)
    st = np.stack([audio, audio], axis=1)
    pcm = (st * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return path


# ===========================================================================
# 6) CAPTIONS — SRT builder (word-boundary aware)
# ===========================================================================
def _fmt_ts(sec: float) -> str:
    ms = int(round(max(sec, 0) * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(scenes: list[dict], path: Path) -> Path:
    """scenes: [{text, voice_s, dur_s, words?}] with cumulative scene starts."""
    entries: list[tuple[float, float, str]] = []
    t0 = 0.0
    for sc in scenes:
        words = sc.get("words") or []
        start = t0
        if words:
            end = t0 + min(words[-1]["end"] + CAPTION_TAIL_S, sc["dur_s"])
        else:
            end = t0 + min(sc["voice_s"] + 0.5, sc["dur_s"])
        end = max(end, start + 0.4)  # kabhi ulta na ho
        entries.append((start, end, sc["text"]))
        t0 += sc["dur_s"]

    lines = []
    for i, (a, b, txt) in enumerate(entries, 1):
        lines += [str(i), f"{_fmt_ts(a)} --> {_fmt_ts(b)}", txt, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ===========================================================================
# 7) RENDER PIPELINE
# ===========================================================================
def render_video(topic: str, *, lang: str = "hi", scenes_n: int = 3,
                 visual: str = "cards", size: str = "horizontal", engine: str = "auto",
                 music: bool = False, captions: bool = True, notes_dir: Path | None = None,
                 out_dir: Path | None = None, piper_model: Path | None = None,
                 voice: str | None = None, logger=None) -> dict:
    """Full pipeline. Returns report dict (report.json ka content)."""
    if logger is None:
        logger = Logger()
    elif not hasattr(logger, "_stub_warned"):
        logger = Logger(logger)  # plain callable → Logger wrapper
    t_start = time.time()

    ffmpeg = find_ffmpeg(logger)
    if not ffmpeg:
        raise SystemExitCode(2, "ffmpeg nahi mila. Install: `winget install Gyan.FFmpeg` "
                                "(Windows) ya `sudo apt install ffmpeg` (Linux). "
                                "Ya `pip install imageio-ffmpeg` fallback ke liye.")
    tips: list[str] = []
    w, h = SIZES.get(size, SIZES["horizontal"])
    lang = lang if lang in LANGS else "hi"

    out_dir = Path(out_dir or f"output_{slugify(topic)}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / "tmp"
    tmp_dir.mkdir(exist_ok=True)

    # ---- 1. script ----
    logger(f"[1/6] Script bana rahe hain ({scenes_n} scenes, lang={lang})…")
    script = build_script(topic, lang, scenes_n, notes_dir, logger)
    for i, sc in enumerate(script, 1):
        logger(f"      {i:02d} [{sc['title']}] {sc['text'][:70]}")

    # ---- 2. voice per scene ----
    logger("[2/6] Voice generate ho rahi hai (ladder: voicestudio→edge→piper→silent)…")
    scene_rows: list[dict] = []
    for i, sc in enumerate(script, 1):
        vpath = tmp_dir / f"voice_{i}.wav"
        eng, dur_v, words = synth_voice(sc, lang, engine, vpath, piper_model, logger,
                                        voice=voice)
        dur_s = max(dur_v + SCENE_PAD_S, MIN_SCENE_S)
        scene_rows.append({**sc, "idx": i, "voice": eng, "voice_s": round(dur_v, 3),
                           "dur_s": round(dur_s, 3), "words": words, "_voice_path": vpath})
        logger(f"      voice {i}: engine={eng} {dur_v:.2f}s → scene {dur_s:.2f}s")
    used_engines = {r["voice"] for r in scene_rows}

    # ---- 3. visuals per scene ----
    logger(f"[3/6] Visuals ({visual}, {w}x{h})…")
    stock_used = False
    for row in scene_rows:
        i = row["idx"]
        card_path = out_dir / f"card_{i}.png"
        clip = None
        if visual in ("stock", "higgsfield"):
            clip = fetch_stock_clip(topic, i - 1, row["dur_s"], tmp_dir / f"stock_{i}.mp4", logger)
            if clip:
                stock_used = True
            elif visual == "higgsfield":
                tips.append("HIGGSFIELD cloud generation skip hui (key/network) — cards use hue.")
            else:
                tips.append("PEXELS_API_KEY ya PIXABAY_API_KEY daalo — real stock footage ke liye.")
        make_card(i, len(scene_rows), row["title"], row["text"], (w, h), card_path, topic)
        row["visual_path"] = clip if clip else card_path
        row["visual"] = card_path.name
        row["kind"] = "video" if clip else "image"

    # ---- 4. per-scene render ----
    has_subtitles = ffmpeg_has_filter(ffmpeg, "subtitles")
    logger("[4/6] Scenes render (ffmpeg: Ken Burns + voice)…")
    for row in scene_rows:
        i = row["idx"]
        scene_out = tmp_dir / f"scene_{i:02d}.mp4"
        dur = row["dur_s"]
        frames = int(dur * FPS) + 2
        canvas = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x0f172a")
        if row["kind"] == "image":
            # Ken Burns sirf still cards pe — video input pe zoompan frame multiply karta hai
            vchain = (canvas + f",zoompan=z='min(zoom+0.00035,1.10)':d={frames}:"
                      f"s={w}x{h}:fps={FPS}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',"
                      "format=yuv420p")
        else:
            vchain = canvas + f",fps={FPS},format=yuv420p"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-i", str(row["visual_path"]), "-i", str(row["_voice_path"]),
               "-filter_complex", f"[0:v]{vchain}[v];[1:a]apad[a]",
               "-map", "[v]", "-map", "[a]",
               "-t", f"{dur:.3f}", "-r", str(FPS),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
               str(scene_out)]
        run_ffmpeg(cmd, logger)
        row["_scene_path"] = scene_out

    # ---- 5. concat (relative paths — portability fix) ----
    logger("[5/6] Concat + music + captions…")
    concat = tmp_dir / "concat.txt"
    concat.write_text("".join(f"file '{r['_scene_path'].name}'\n" for r in scene_rows),
                      encoding="utf-8")
    base_out = out_dir / "base.mp4"
    run_ffmpeg([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", concat.name,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "160k", str(base_out)],
               logger, cwd=tmp_dir)

    with_music = base_out
    if music:
        mpath = tmp_dir / "music.wav"
        if synth_music(mpath, sum(r["dur_s"] for r in scene_rows), logger):
            mixed = out_dir / "mixed.mp4"
            total = sum(r["dur_s"] for r in scene_rows)
            run_ffmpeg([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(base_out), "-i", str(mpath),
                        "-filter_complex",
                        f"[1:a]volume=0.16,afade=t=out:st={max(total - 2, 0):.2f}:d=2[m];"
                        f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=3[a]",
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "160k", str(mixed)],
                       logger)
            with_music = mixed

    # captions burn (libass) — warna sidecar .srt + tip
    final = out_dir / "vaanireel_video.mp4"
    subs_srt = out_dir / "subs.srt"
    clean_out = out_dir / "vaanireel_video_clean.mp4"
    shutil.copyfile(with_music, clean_out)
    build_srt([{k: v for k, v in r.items() if not k.startswith("_")} for r in scene_rows],
              subs_srt)

    captions_burned = False
    if captions and has_subtitles:
        sub_final = out_dir / "final_sub.mp4"
        fs = 30 if size == "vertical" else 34
        style = (f"FontName=DejaVu Sans,FontSize={fs},PrimaryColour=&H00FFFFFF,"
                 "OutlineColour=&HAA000000,BorderStyle=1,Outline=2,Shadow=1,"
                 "Alignment=2,MarginV=42")
        try:
            run_ffmpeg([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(with_music),
                        "-vf", f"subtitles={subs_srt.name}:force_style='{style}'",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-c:a", "copy", str(sub_final.name)],
                       logger, cwd=out_dir)
            captions_burned = sub_final.exists() and sub_final.stat().st_size > 1000
            if captions_burned:
                shutil.move(str(sub_final), str(final))
        except RenderError as e:
            logger(f"[!] caption burn failed: {e} — sidecar .srt use karo")
    elif captions and not has_subtitles:
        tips.append("Is ffmpeg build me libass nahi hai — captions sidecar subs.srt me hain.")

    if not captions_burned:
        shutil.copyfile(with_music, final)

    # ---- 6. report ----
    total_s = probe_duration(ffmpeg, final) or round(sum(r["dur_s"] for r in scene_rows), 2)
    if len(scene_rows) >= 3 and not stock_used and all(r["kind"] == "image" for r in scene_rows):
        risk = "HIGH"
        tips.append("Saare visuals cards hain — slideshow feel aa sakta hai. "
                    "PEXELS_API_KEY ya HIGGSFIELD_API_KEY daalo, ya scenes ko 3-4 hi rakho.")
    else:
        risk = "LOW"
    if not captions_burned and captions:
        tips.append("Captions burn nahi hue — accessibility ke liye subs.srt sidecar available hai.")
    if total_s < 8:
        tips.append("Reel thodi lambi rakho (<8s) — retention ke liye 12–25s sweet spot hai.")
    if "silent" in used_engines:
        tips.append("Voice silent stub hai — edge-tts install karo (pip install edge-tts) "
                    "ya VoiceStudio app chalu rakho.")

    report = {
        "tool": TOOL,
        "version": VERSION,
        "topic": topic,
        "lang": lang,
        "voice_engine": ("silent" if used_engines == {"silent"} else
                         "+".join(sorted(used_engines - {"silent"})) or "silent"),
        "visual_mode": visual,
        "size": size,
        "scenes": [
            {"idx": r["idx"], "title": r["title"], "voice": r["voice"],
             "voice_s": r["voice_s"], "dur_s": r["dur_s"], "kind": r["kind"],
             "visual": r["visual"], "text": r["text"]}
            for r in scene_rows
        ],
        "output": os.path.relpath(final, Path.cwd()),
        "quality": {"total_s": round(total_s, 2), "slideshow_risk": risk, "tips": tips},
        "hardware_note": HARDWARE_NOTE,
        "render_s": round(time.time() - t_start, 2),
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    logger(f"[6/6] Done → {final}  ({total_s:.2f}s, {report['render_s']}s render)")
    return report


class RenderError(Exception):
    pass


class SystemExitCode(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def run_ffmpeg(cmd: list[str], logger, cwd: Path | None = None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(cwd) if cwd else None)
        if p.returncode != 0:
            raise RenderError(p.stderr.strip()[-400:] or f"ffmpeg exit {p.returncode}")
    except FileNotFoundError as e:
        raise RenderError(f"ffmpeg executable missing: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise RenderError("ffmpeg timeout (600s)") from e


# ===========================================================================
# 8) DOCTOR — "aapke system pe kya available hai"
# ===========================================================================
def doctor_checks() -> list[dict]:
    rows: list[dict] = []

    def add(name: str, ok: bool, note: str, hint: str = "", essential: bool = True):
        status = "ok" if ok else ("warn" if not essential else "fail")
        rows.append({"name": name, "status": status, "note": note, "hint": hint,
                     "essential": essential})

    v = sys.version_info
    add("Python", v >= (3, 10), f"{v.major}.{v.minor}.{v.micro}",
        "3.10+ chahiye — python.org se update karo" if v < (3, 10) else "")

    ff = find_ffmpeg()
    add("FFmpeg", bool(ff), ff or "nahi mila",
        "winget install Gyan.FFmpeg (Win) / apt install ffmpeg (Linux) / pip install imageio-ffmpeg"
        if not ff else "")

    for mod, name, hint in (
        ("PIL", "Pillow (cards)", "pip install pillow"),
        ("numpy", "numpy (music/DSP)", "pip install numpy"),
        ("edge_tts", "edge-tts (free voice)", "pip install edge-tts"),
    ):
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            add(name, True, f"installed ({ver})")
        except ImportError:
            add(name, False, "nahi mila", hint)

    vs = voicestudio_available()
    add("VoiceStudio app", vs, "chalu hai (port 3900)" if vs else "off (optional)",
        "voicestudio.sh installer se install + app chalu rakho" if not vs else "",
        essential=False)

    pp = shutil.which("piper")
    add("piper (offline voice)", bool(pp), pp or "nahi mila (optional)",
        "pip install piper-tts" if not pp else "", essential=False)

    font_dir = _font_dir()
    dejavu = bool(font_dir and (font_dir / "DejaVuSans-Bold.ttf").exists())
    add("Fonts (DejaVu)", dejavu, str(font_dir) if dejavu else "nahi mile",
        "apt install fonts-dejavu-core" if not dejavu else "")

    ffprobe = find_ffprobe()
    add("ffprobe (optional)", bool(ffprobe), ffprobe or "nahi hai — duration fallback use hoga",
        essential=False)

    add("PEXELS_API_KEY", bool(os.environ.get("PEXELS_API_KEY")),
        "set" if os.environ.get("PEXELS_API_KEY") else "nahi hai (optional — stock visuals)",
        "export PEXELS_API_KEY=...", essential=False)
    add("PIXABAY_API_KEY", bool(os.environ.get("PIXABAY_API_KEY")),
        "set" if os.environ.get("PIXABAY_API_KEY") else "nahi hai (optional fallback)",
        essential=False)
    add("HIGGSFIELD_API_KEY", bool(os.environ.get("HIGGSFIELD_API_KEY")),
        "set" if os.environ.get("HIGGSFIELD_API_KEY") else "nahi hai (optional — cloud visuals)",
        essential=False)
    return rows


# ===========================================================================
# 9) AUDIT — 17 repos ka table
# ===========================================================================
def audit_rows() -> list[dict]:
    return [
        {"repo": "FFmpeg/FFmpeg",            "role": "assembler (encode/concat/captions)", "status": "REQUIRED", "note": "core dependency"},
        {"repo": "python/cpython",           "role": "runtime", "status": "REQUIRED", "note": "3.10+"},
        {"repo": "python-pillow/Pillow",     "role": "card visuals", "status": "REQUIRED", "note": "PIL images"},
        {"repo": "numpy/numpy",              "role": "music synth / DSP", "status": "REQUIRED", "note": "ambient pad"},
        {"repo": "rany2/edge-tts",           "role": "free neural voice (cloud)", "status": "REQUIRED", "note": "ladder step 2"},
        {"repo": "microsoft/playwright",     "role": "e2e browser tests", "status": "REQUIRED(dev)", "note": "tests/e2e"},
        {"repo": "pytest-dev/pytest",        "role": "unit tests", "status": "REQUIRED(dev)", "note": "tests/"},
        {"repo": "imageio/imageio-ffmpeg",   "role": "bundled ffmpeg fallback", "status": "FALLBACK", "note": "jab system ffmpeg na ho"},
        {"repo": "rhasspy/piper",            "role": "offline voice", "status": "OPTIONAL", "note": "ladder step 3"},
        {"repo": "google/fonts",             "role": "Noto Devanagari captions", "status": "OPTIONAL", "note": "hindi glyphs"},
        {"repo": "notofonts/devanagari",     "role": "devanagari font source", "status": "OPTIONAL", "note": "caption rendering"},
        {"repo": "Pexels API (web service)", "role": "stock footage", "status": "OPTIONAL", "note": "PEXELS_API_KEY"},
        {"repo": "pixabay/python",           "role": "stock footage fallback", "status": "OPTIONAL", "note": "PIXABAY_API_KEY"},
        {"repo": "SYSTRAN/faster-whisper",   "role": "ASR captions (future)", "status": "FUTURE", "note": "voiceover se auto-captions"},
        {"repo": "openai/whisper",           "role": "ASR alt (future)", "status": "FUTURE", "note": "heavy for 6GB"},
        {"repo": "coqui-ai/TTS",             "role": "local TTS alt", "status": "SKIP", "note": "6GB pe XTTS risky — edge/piper kaafi"},
        {"repo": "Zulko/moviepy",            "role": "editor abstraction", "status": "SKIP", "note": "direct ffmpeg use — overhead nahi chahiye"},
    ]


# ===========================================================================
# 10) CLI
# ===========================================================================
def _print_doctor(logger) -> int:
    logger("\n== DOCTOR — aapke system pe kya available hai ==\n")
    rows = doctor_checks()
    icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}
    fails = 0
    for r in rows:
        logger(f"{icon[r['status']]} {r['name']:<26} {r['note']}")
        if r["hint"]:
            logger(f"   ↳ {r['hint']}")
        if r["status"] == "fail":
            fails += 1
    logger(f"\n{fails} essential cheez missing."
           if fails else "\nSab essential available — render ready! "
                         "(optional items warn ⚠️ se dikhte hain)")
    return 0 if not fails else 2


def _print_audit(logger) -> int:
    logger("\n== AUDIT — 17 repos ka audit table ==\n")
    rows = audit_rows()
    w = (30, 44, 14)
    logger(f"{'REPO':<{w[0]}} {'ROLE':<{w[1]}} {'STATUS':<{w[2]}} NOTE")
    logger("-" * 110)
    for r in rows:
        logger(f"{r['repo']:<{w[0]}} {r['role']:<{w[1]}} {r['status']:<{w[2]}} {r['note']}")
    logger("")
    return 0


def _print_dry_run(args, logger) -> int:
    ffmpeg = find_ffmpeg()
    logger("\n== DRY RUN — plan (kuch render nahi hoga) ==\n")
    plan = {
        "topic": args.topic,
        "lang": args.lang,
        "size": args.size,
        "canvas": SIZES[args.size],
        "visual": args.visual,
        "engine_ladder": {"auto": ["voicestudio", "edge", "piper", "silent"],
                          "voicestudio": ["voicestudio", "silent"],
                          "edge": ["edge", "silent"],
                          "piper": ["piper", "silent"],
                          "silent": ["silent"]}[args.engine],
        "engines_available": {
            "ffmpeg": bool(ffmpeg),
            "voicestudio": voicestudio_available(),
            "edge_tts": importlib_ok("edge_tts"),
            "piper": bool(shutil.which("piper")),
        },
        "music": args.music,
        "captions": not args.no_captions,
        "notes": str(args.notes) if args.notes else None,
        "output_dir": args.out or f"output_{slugify(args.topic)}",
        "scenes_est": [],
    }
    script = build_script(args.topic, args.lang, args.scenes, args.notes, logger)
    for i, sc in enumerate(script, 1):
        est = max(len(sc["text"].split()) * 0.32 + SCENE_PAD_S, MIN_SCENE_S)
        plan["scenes_est"].append({"idx": i, "title": sc["title"],
                                   "text": sc["text"][:60], "est_s": round(est, 2)})
    plan["est_total_s"] = round(sum(s["est_s"] for s in plan["scenes_est"]), 2)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def importlib_ok(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=TOOL, description="script → voice → visuals → captions → MP4")
    ap.add_argument("topic", nargs="?", help="video ka topic")
    ap.add_argument("--lang", default="hi", choices=LANGS, help="script language (default: hi)")
    ap.add_argument("--scenes", type=int, default=3, help="scene count 3-6 (default 3)")
    ap.add_argument("--visual", default="cards", choices=VISUALS, help="visual mode (default cards)")
    ap.add_argument("--size", default="horizontal", choices=list(SIZES), help="canvas (default horizontal)")
    ap.add_argument("--engine", default="auto", choices=ENGINES, help="voice engine (default auto ladder)")
    ap.add_argument("--voice", default=None, help="edge-tts voice override (e.g. hi-IN-MadhurNeural)")
    ap.add_argument("--piper-model", default=None, type=Path, help="piper .onnx model path")
    ap.add_argument("--notes", default=None, type=Path, help="knowledge folder (.txt/.md) — grounded script")
    ap.add_argument("--music", action="store_true", help="ambient background music mix")
    ap.add_argument("--no-captions", action="store_true", help="captions burn off (sidecar .srt still banega)")
    ap.add_argument("--out", default=None, help="output folder override (default output_<topic>)")
    ap.add_argument("--doctor", action="store_true", help="system check table")
    ap.add_argument("--audit", action="store_true", help="17 repos ka audit table")
    ap.add_argument("--dry-run", action="store_true", help="plan dekho, render nahi")
    ap.add_argument("--version", action="version", version=f"{TOOL} {VERSION}")
    args = ap.parse_args(argv)

    logger = Logger()

    try:
        if args.doctor:
            return _print_doctor(logger)
        if args.audit:
            return _print_audit(logger)
        if args.dry_run:
            if not args.topic:
                ap.error("--dry-run ke liye topic chahiye")
            return _print_dry_run(args, logger)
        if not args.topic:
            ap.error("topic do, ya --doctor / --audit / --dry-run chalao")
        if not (3 <= args.scenes <= 6):
            ap.error("--scenes 3 se 6 ke beech do")

        render_video(
            args.topic, lang=args.lang, scenes_n=args.scenes, visual=args.visual,
            size=args.size, engine=args.engine, music=args.music,
            captions=not args.no_captions, notes_dir=args.notes, out_dir=args.out,
            piper_model=args.piper_model, voice=args.voice, logger=logger,
        )
        return 0
    except SystemExitCode as e:
        logger(f"[X] {e.message}")
        return e.code
    except RenderError as e:
        logger(f"[X] render failed: {e}")
        return 3
    except Exception as e:  # noqa: BLE001 — clean error, full traceback sirf DEBUG me
        if os.environ.get("VC_DEBUG"):
            raise
        logger(f"[X] pipeline error: {type(e).__name__}: {e}")
        logger("    (poora traceback dekhne ke liye: VC_DEBUG=1)")
        return 3


if __name__ == "__main__":
    code = main()
    if Logger.broken_pipe:
        # downstream pipe band hai — stdout ko devnull pe redirect karo warna
        # interpreter-shutdown flush exit code 120 deta hai
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
    sys.exit(code)
