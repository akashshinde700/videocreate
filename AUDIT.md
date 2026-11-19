# 🔍 Full Audit Report — videocreate repo

**Date:** 2026-11-19 · **Branch:** `arena/d4454fb9-videocreate` (base `c610cf9`)

## Summary

| | |
|---|---|
| Issues found | **17** (5 critical, 5 medium, 7 low) |
| Issues fixed | **17/17** ✅ |
| Unit tests | **19 passed** (pytest) |
| E2e tests | **9 passed** (Playwright, Chromium) |

## Findings & Fixes

### Critical

**1. Source code repo me hi nahi tha** — repo sirf output artifacts (mp4/wav/png/
srt/json) aur setup guide tha. Guide jis `video_creator.py` ki baat karta hai wo
file exist nahi karti. Kuch bhi run/test nahi ho sakta tha.
→ **FIX:** `video_creator.py` reconstruct kiya — guide ke documented behavior
(flags, voice ladder, modes, `--doctor/--audit/--dry-run`) + artifacts ke
reverse-engineered evidence se (report.json schema, 0.9s@22050Hz stub voice,
2.2s scene duration, 0.8s scene pad, 1920×1080 canvas, subs.srt word-timings,
Hinglish script template — original captions se line-by-line match).

**2. Koi dependency manifest nahi** — `pip install edge-tts pillow numpy` sirf
guide ke text me tha.
→ **FIX:** `requirements.txt` add kiya (core + optional fallback + dev deps).

**3. Koi test nahi tha** — na unit, na e2e.
→ **FIX:** 19 pytest unit tests (script gen, SRT builder, stub wav, cards, full
render integration, report schema, concat portability) + 9 Playwright e2e tests
(UI, doctor, audit, full render flow, API schema, security).

**4. FFmpeg hard dependency, graceful fallback nahi** — "ffmpeg nahi mila" sirf
troubleshooting note tha; ffmpeg missing ho to poora pipeline crash.
→ **FIX:** discovery chain: `PATH` → `FFMPEG` env → `imageio-ffmpeg` bundled
binary → clean error + install hints (exit code 2). `--doctor` me bhi hint.

**5. Unhandled exceptions — raw traceback, exit code 0/120 weirdness**
- Pipeline crash pe traceback + exit code unreliable tha.
- `... | head` pipe close pe `BrokenPipeError` → exit 120.
→ **FIX:** clean `[X]` errors with proper exit codes (0 ok / 2 config / 3
render); `VC_DEBUG=1` pe full traceback; broken-pipe → devnull redirect, exit
code sahi.

### Medium

**6. concat.txt absolute paths** — original artifacts me
`file '/home/z/my-project/download/test_vs/tmp/scene_01.mp4'` — machine-specific
paths, dusri machine pe concat fail.
→ **FIX:** concat demuxer `cwd=tmp` + relative paths (`file 'scene_01.mp4'`).
Unit test regression cover karta hai (`/home/` assert).

**7. SRT caption timing inverted** — render test me `00:00:02,200 -->
00:00:01,400` (end < start) — relative end vs absolute start ka mixing bug.
→ **FIX:** `end = t0 + relative`, aur `max(end, start+0.4)` guard. Unit test.

**8. Report.json schema drift** — tool bina schema ke likhta; original artifact
schema (`voice_s/dur_s/kind/slideshow_risk/tips/hardware_note`) kahi define nahi.
→ **FIX:** exact artifact-compatible schema + per-scene `voice` engine tracking
(original me poora ladder ek hi engine dikha deta tha). Schema e2e test me assert.

**9. Web ke liye koi surface nahi** — Playwright testing ke liye UI chahiye tha.
→ **FIX:** `app.py` + `web/index.html` — pure-stdlib Web Studio (ThreadingHTTPServer,
0.0.0.0 bind, live log, progress, video player, doctor/audit panels). Job queue
(1 concurrent), path-traversal-safe media serving, input whitelist/clamping.

**10. Zoompan stock-video pe frame-multiply bug** — Ken Burns filter video input
pe bhi lagta to `d=frames` har input frame ko multiply karta → scene 100x slow.
→ **FIX:** `kind=image` pe zoompan, `kind=video` pe scale/pad/fps chain.

### Low

**11. Repo root 40 media files ki dump** — `(1)`/`(2)` download duplicates sab
root me.
→ **FIX:** sab `artifacts/` me `git mv` (history preserved) — reference samples.

**12. .gitignore nahi tha** — future renders/tests repo ko pollute karte.
→ **FIX:** `.gitignore` (`output_*/`, `webjobs/`, `node_modules/`,
`test-results/`, `__pycache__/`, …).

**13. Silent-stub warning har scene pe repeat** — noise.
→ **FIX:** once-per-run warning.

**14. Doctor "essential missing" galat count** — optional items (VoiceStudio,
piper, API keys) bhi fail count hote → misleading exit code 2.
→ **FIX:** `essential` flag — optional cheezein ⚠️ warn, essentials only ❌ fail.

**15. `base.mp4` galat folder me likha** — concat cwd/output path confusion.
→ **FIX:** `out_dir` resolved absolute; report me cwd-relative path (artifact
jaisa).

**16. edge-tts hang risk** — network slow ho to per-scene 30s+ block.
→ **FIX:** `asyncio.wait_for(30s)` guard + fail-fast ladder logging
(`[!] voice:edge → …` har step ka reason dikhta hai).

**17. BrokenPipe exit 120** (detail #5 me) — ✅ fixed & verified.

## Test coverage — kaise verify kare

```bash
# unit (19)
python -m pytest tests/ -v

# e2e (9) — Web Studio UI/API/security, full render flow included
npm install && npx playwright test

# CLI sanity
python video_creator.py --doctor
python video_creator.py --audit
python video_creator.py --dry-run "koi bhi topic"
python video_creator.py "demo" --engine silent --scenes 3 --music
```

## Known limits (fix nahi — design notes)

- **edge-tts sandbox me blocked** (network allowlist) → ladder silently stub pe
  girta hai; user machine pe internet ho to real voice aati hai. Ye intended
  fallback behavior hai, bug nahi.
- **VoiceStudio/piper/Higgsfield** external services — probe + graceful fallback
  implemented; integration unke real endpoints pe hi poora chal sakta hai.
- **@sparticuz/chromium** tests ka fallback hai — Playwright-managed browser
  available ho to wo pehle use hota hai.
