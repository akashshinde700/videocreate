"""Unit tests — video_creator.py pipeline internals (pytest).

Chalao:  python3 -m pytest tests/ -v
"""

import json
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_creator import (  # noqa: E402
    MIN_SCENE_S,
    SCENE_PAD_S,
    STUB_VOICE_S,
    audit_rows,
    build_srt,
    build_script,
    find_ffmpeg,
    make_card,
    render_video,
    slugify,
    _fmt_ts,
    _write_stub_wav,
)


# ---------------------------------------------------------------------------
# script generator
# ---------------------------------------------------------------------------
def test_build_script_scene_count_and_titles():
    for n in (3, 4, 5, 6):
        script = build_script("gpu guide", "hi", n, None)
        assert len(script) == n
        assert script[0]["title"] == "Hook"
        assert script[-1]["title"] == "Outro"
        assert all(s["text"].strip() for s in script)


def test_build_script_topic_aas_text_me():
    script = build_script("cloud gaming", "hi", 3, None)
    assert "cloud gaming" in script[0]["text"]
    assert "cloud gaming" in script[-1]["text"]


def test_build_script_scenes_clamp():
    assert len(build_script("x", "hi", 1, None)) == 3      # min 3
    assert len(build_script("x", "hi", 10, None)) == 6     # max 6


def test_build_script_english():
    script = build_script("rust vs go", "en", 3, None)
    assert "Today's topic: rust vs go" in script[0]["text"]


def test_notes_grounding(tmp_path):
    notes = tmp_path / "knowledge"
    notes.mkdir()
    (notes / "a.md").write_text(
        "Cloud gaming latency depends on region. "
        "The server picks the nearest datacenter automatically. "
        "Unrelated filler sentence about pasta recipes.", "utf-8")
    script = build_script("cloud gaming", "hi", 3, notes)
    points = [s["text"] for s in script if s["title"].startswith("Point")]
    assert any("datacenter" in p or "latency" in p for p in points), \
        f"grounded point chahiye tha, mila: {points}"


def test_notes_folder_missing_falls_back(tmp_path):
    script = build_script("topic", "hi", 3, tmp_path / "nahi_hai_ye_folder")
    assert len(script) == 3  # crash nahi — generic script


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def test_slugify():
    assert slugify("6GB GPU Guide!") == "6gb_gpu_guide"
    assert slugify("हिंदी topic") == "topic"  # non-ascii strip → fallback safe
    assert slugify("") == "video"


def test_fmt_ts():
    assert _fmt_ts(0) == "00:00:00,000"
    assert _fmt_ts(8.328) == "00:00:08,328"
    assert _fmt_ts(3661.5) == "01:01:01,500"


def test_stub_wav_matches_artifact(tmp_path):
    """Repo artifact voice_1.wav jaisa: 0.9s @ 22050Hz mono 16bit."""
    p = tmp_path / "stub.wav"
    dur = _write_stub_wav(p)
    assert dur == pytest.approx(STUB_VOICE_S)
    with wave.open(str(p), "rb") as w:
        assert w.getframerate() == 22050
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() == 19845  # 22050 * 0.9


def test_find_ffmpeg_available():
    assert find_ffmpeg() is not None


def test_audit_17_repos():
    rows = audit_rows()
    assert len(rows) == 17
    assert all({"repo", "role", "status", "note"} <= set(r) for r in rows)


# ---------------------------------------------------------------------------
# srt builder — inverted-timing regression (audit fix)
# ---------------------------------------------------------------------------
def _scene(text, voice_s, dur_s, words=None):
    return {"text": text, "voice_s": voice_s, "dur_s": dur_s, "words": words or []}


def test_srt_timings_monotonic_no_inversion(tmp_path):
    scenes = [
        _scene("one", 0.9, 2.2),
        _scene("two", 0.9, 2.2),
        _scene("three", 0.9, 2.2),
    ]
    out = tmp_path / "subs.srt"
    build_srt(scenes, out)
    text = out.read_text("utf-8")
    blocks = [b for b in text.strip().split("\n\n") if b.strip()]
    assert len(blocks) == 3

    def parse(ts):
        h, m, rest = ts.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    prev_end = 0.0
    for b in blocks:
        lines = b.splitlines()
        a, z = lines[1].split(" --> ")
        start, end = parse(a), parse(z)
        assert end > start, f"inverted caption timing: {a} --> {z}"
        assert start >= prev_end
        prev_end = end


def test_srt_word_boundaries_clipped_to_scene(tmp_path):
    words = [{"start": 0.0, "end": 9.5, "word": "x"}]  # voice > scene dur
    scenes = [_scene("long speech", 9.5, 10.3, words)]
    out = tmp_path / "s.srt"
    build_srt(scenes, out)
    assert "00:00:00,000 -->" in out.read_text("utf-8")


# ---------------------------------------------------------------------------
# cards (PIL)
# ---------------------------------------------------------------------------
CARD_SIZES = [(1920, 1080), (1080, 1920), (1080, 1080)]


@pytest.mark.parametrize("w,h", CARD_SIZES)
def test_make_card_dimensions(tmp_path, w, h):
    out = tmp_path / f"card_{w}x{h}.png"
    make_card(1, 3, "Hook", "test text", (w, h), out, "topic")
    from PIL import Image
    im = Image.open(out)
    assert im.size == (w, h)
    assert out.stat().st_size > 1000


def test_make_card_scene_variants(tmp_path):
    for i in range(1, 4):
        out = tmp_path / f"card_{i}.png"
        make_card(i, 3, f"Point {i}", f"text {i}", (1920, 1080), out, "t")
        assert out.exists()


# ---------------------------------------------------------------------------
# full render integration (silent stub — fast, deterministic)
# ---------------------------------------------------------------------------
def test_render_full_pipeline(tmp_path):
    report = render_video(
        "audit reel", engine="silent", scenes_n=3, size="horizontal",
        visual="cards", music=True, captions=True, out_dir=tmp_path / "out",
        logger=lambda *_: None,
    )
    out = tmp_path / "out"

    # report schema (repo artifact report.json jaisa)
    for key in ("tool", "version", "topic", "lang", "voice_engine", "visual_mode",
                "scenes", "output", "quality", "hardware_note"):
        assert key in report
    assert report["voice_engine"] == "silent"
    assert len(report["scenes"]) == 3
    sc = report["scenes"][0]
    for key in ("idx", "title", "voice", "voice_s", "dur_s", "kind", "visual"):
        assert key in sc
    assert sc["voice_s"] == pytest.approx(STUB_VOICE_S)
    assert sc["dur_s"] == pytest.approx(max(STUB_VOICE_S + SCENE_PAD_S, MIN_SCENE_S))

    # outputs exist
    final = out / "vaanireel_video.mp4"
    assert final.exists() and final.stat().st_size > 50_000
    assert (out / "subs.srt").exists()
    assert (out / "report.json").exists()
    assert (out / "card_1.png").exists()

    # concat.txt — portability fix: RELATIVE paths (koi /home/... nahi)
    concat = (out / "tmp" / "concat.txt").read_text("utf-8")
    lines = [l for l in concat.strip().splitlines() if l]
    assert len(lines) == 3
    assert all(l.startswith("file 'scene_") and l.endswith(".mp4'") for l in lines)
    assert "/home/" not in concat and not any(l.startswith("file '/") for l in lines)

    # srt valid
    srt = (out / "subs.srt").read_text("utf-8")
    assert srt.count("-->") == 3

    # video duration sane
    ff = find_ffmpeg()
    import re
    import subprocess
    r = subprocess.run([ff, "-hide_banner", "-i", str(final)],
                       capture_output=True, text=True, timeout=60)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", r.stderr)
    assert m, "duration parse failed"
    h, mn, s = m.groups()
    total = int(h) * 3600 + int(mn) * 60 + float(s)
    assert 4.0 < total < 15.0


def test_render_report_persists_json(tmp_path):
    report = render_video(
        "json check", engine="silent", scenes_n=3, size="horizontal",
        visual="cards", music=False, captions=False, out_dir=tmp_path / "out2",
        logger=lambda *_: None,
    )
    saved = json.loads((tmp_path / "out2" / "report.json").read_text("utf-8"))
    assert saved["topic"] == report["topic"]
    assert saved["quality"]["total_s"] == report["quality"]["total_s"]
