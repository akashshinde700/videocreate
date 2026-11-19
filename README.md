# 🎬 Vaanireel — video_create

Script → voice → visuals → captions → FFmpeg → **MP4**. Ek hi file ka pipeline
(`video_creator.py`) + Web Studio UI (`app.py`) + tests (pytest + Playwright).

> **6GB VRAM / 16GB RAM friendly** — pipeline CPU-only hai, GPU load zero.
> Heavy video generation cloud/app me (optional).

## Quick start

```bash
pip install -r requirements.txt        # pillow, numpy, edge-tts (+ imageio-ffmpeg fallback)
python video_creator.py "aaj ka topic" --lang hi
# → output_aaj_ka_topic/vaanireel_video.mp4 + report.json
```

Bina kisi API key ke video ban jayegi (edge-tts free neural voice + PIL cards).

## Web Studio

```bash
python app.py                          # http://localhost:8788  (bind 0.0.0.0)
```

Browser se topic likho → Render karo → live log, progress bar, video player,
scene thumbnails, quality tips. Doctor (system check) aur Audit (17-repo table)
panels bhi UI me.

## Voice ladder (auto fallback)

```
voicestudio (localhost:3900 app)  →  edge-tts (free, internet)
  →  piper (offline, model)  →  silent stub (pipeline test)
```

Har scene apna engine report.json me log karta hai. Silent stub = repo ke
`artifacts/voice_1.wav` jaisa 0.9s placeholder — pipeline dry-run testing ke liye.

## Visual modes

| Mode | Kya chahiye | Output |
|---|---|---|
| `--visual cards` (default) | kuch nahi | PIL gradient cards (Ken Burns motion) |
| `--visual stock` | `PEXELS_API_KEY` / `PIXABAY_API_KEY` | free stock footage (key na ho to card fallback) |
| `--visual higgsfield` | `HIGGSFIELD_API_KEY` + `HF_API_BASE_URL` | cloud generation (paid) |

## Handy commands

```bash
python video_creator.py --doctor               # system check table
python video_creator.py --audit                # 17 repos ka audit table
python video_creator.py --dry-run "topic"      # plan dekho, render nahi
python video_creator.py "topic" --scenes 5 --music --size vertical   # Shorts
python video_creator.py "topic" --notes ./knowledge                  # grounded script
python video_creator.py "topic" --engine piper --piper-model m.onnx  # fully offline
python video_creator.py "topic" --no-captions  # captions burn off (sidecar .srt banega)
```

## Tests

```bash
python -m pytest tests/ -v          # 19 unit tests (script/srt/cards/render/report)
npm install                         # @playwright/test + @sparticuz/chromium
npx playwright test                 # 9 e2e tests — Web Studio UI + API + security
```

E2e tests chromium dhoondte hain is order me:
1. Playwright-managed browser (`npx playwright install chromium`)
2. System chrome/chromium
3. `@sparticuz/chromium` npm package ka bundled binary (Linux fallback —
   `--single-process/--no-zygote` args filter hote hain, warna unstable)

## Repo structure

```
video_creator.py     # poora pipeline (CLI + importable API)
app.py               # Web Studio server (pure stdlib, port 8788)
web/index.html       # Web Studio UI
tests/test_pipeline.py   # pytest unit tests (19)
tests/e2e/           # Playwright specs (9) + fixtures
artifacts/           # reference output samples (original repo dumps)
SETUP_GUIDE_6GB.md   # local setup guide
AUDIT.md             # full audit — issues & fixes
```

## Report.json (har render ke saath)

`tool, version, topic, lang, voice_engine, visual_mode, size, scenes[]
{idx,title,voice,voice_s,dur_s,kind,visual,text}, output,
quality {total_s, slideshow_risk, tips[]}, hardware_note, render_s`

`slideshow_risk: HIGH` jab saare visuals cards ho — tips API key / scene-count
suggestion deti hain.
