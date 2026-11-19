# Local Setup Guide — 6GB VRAM / 16GB RAM ke liye

> **Seedha jawab:** Aapke system pe locally sirf **2 cheezein** chalani hain —
> **1) `video_creator.py`** (poora product, GPU pe load zero) aur
> **2) VoiceStudio app** (optional, best voice quality, ~2GB VRAM).
> Heavy video generation (Kling/Seedance) cloud me hoti hai — aapki GPU free rehti hai.

---

## Step 1 — video_creator.py (MAIN, zaroori)

Ye file hi poora pipeline hai: script → voice → visuals → captions → FFmpeg → MP4.

### Windows (PowerShell)
```powershell
# Python 3.10+ (agar nahi hai)
winget install Python.Python.3.12

# FFmpeg (assembly ka core)
winget install Gyan.FFmpeg

# Voice + cards + music ke light dependencies (ek hi line)
pip install edge-tts pillow numpy

# Chalao!
python video_creator.py "aaj ka topic" --lang hi
```

### Linux (bash)
```bash
sudo apt install -y ffmpeg python3-pip
pip install edge-tts pillow numpy
python3 video_creator.py "aaj ka topic" --lang hi
```

**Bas.** Bina kisi API key ke video ban jayegi (edge-tts free neural voice + generated cards).
Output folder: `output_<topic>/vaanireel_video.mp4` + `report.json`.

---

## Step 2 — VoiceStudio app (OPTIONAL, voice quality upgrade)

ElevenLabs-jaisi local voice + Hindi voice cloning. GGUF engine (OmniVoice)
**automatically aapke 6GB ke hisaab se model size chunta hai** (~1.5–2GB VRAM — comfortably fit).

```powershell
# Windows
irm https://voicestudio.sh/install.ps1 | iex
```
```bash
# Linux
curl -fsSL https://voicestudio.sh/install | sh
```

App kholo → engine me **OmniVoice** chuno → app ko chalu rakho (port 3900).
Ab `video_creator.py` **automatically** isko use karega (voice ladder: voicestudio → edge → piper → silent).
Cloning: app me 10–15 sec clean reference recording do → wahi voice aapki video me.

---

## Step 3 — Visuals ke options (sab optional)

| Mode | Kya chahiye | Kahan chalega |
|---|---|---|
| `--visual cards` (default) | kuch nahi | **Aapke CPU pe** (PIL) |
| `--visual stock` | free key: `PEXELS_API_KEY` ya `PIXABAY_API_KEY` | download CPU pe, video cloud pe free |
| `--visual higgsfield` | `HIGGSFIELD_API_KEY=id:secret` + `HF_API_BASE_URL` (paid) | **cloud** — aapki GPU free |

```bash
# Stock footage ka example
export PEXELS_API_KEY="apni_key"
python video_creator.py "topic" --visual stock

# Notes se grounded script (knowledge folder: .txt/.md)
python video_creator.py "topic" --notes ./knowledge
```

---

## Aapke 6GB GPU / 16GB RAM pe kya-kya chalega (budget table)

| Component | VRAM | RAM | Status |
|---|---|---|---|
| video_creator.py (cards mode) | **0 GB** (CPU-only) | ~1 GB | ✅ hamesha chalega |
| edge-tts voice | 0 GB (cloud) | ~0.2 GB | ✅ hamesha chalega |
| VoiceStudio OmniVoice GGUF | ~1.5–2 GB | ~3 GB | ✅ 6GB me comfortable |
| FFmpeg encode (1080p) | 0 GB | ~1.5 GB | ✅ |
| piper (offline voice) | 0 GB (CPU) | ~0.5 GB | ✅ |
| Local video models (Helios, FreeVideo) | 8 GB+ | 16 GB+ | ❌ **mat chalana** — cloud use karo |

**Total worst case:** ~2GB VRAM + ~5GB RAM — aapke 6GB/16GB me sab comfortable.
Do baar render ek saath bhi chalega.

---

## Handy commands

```bash
python video_creator.py --doctor      # aapke system pe kya available hai
python video_creator.py --audit      # 17 repos ka audit table
python video_creator.py --dry-run "topic"   # plan dekho bina render kiye
python video_creator.py "topic" --scenes 5 --music --size vertical   # Shorts
python video_creator.py "topic" --engine piper --piper-model ./model.onnx  # fully offline
```

## Troubleshooting

- **"ffmpeg nahi mila"** → install karke terminal dobara kholo (PATH me hona chahiye)
- **Voice silent aayi** → `pip install edge-tts` chalao, ya VoiceStudio app chalu rakho
- **Devanagari captions** → Noto Devanagari font install ho to auto use hoga (optional)
- Kisi bhi waqt `--doctor` chalao — har missing cheez ka hint milta hai
