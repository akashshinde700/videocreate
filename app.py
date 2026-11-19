#!/usr/bin/env python3
"""
app.py — Vaanireel Web Studio
=============================
video_creator.py pipeline ka web wrapper (pure stdlib, zero extra dependency).

    python app.py                # http://localhost:8788  (bind 0.0.0.0)
    PORT=9000 python app.py

Endpoints:
    GET  /                → Web Studio UI
    GET  /api/health      → {ok, ffmpeg, version}
    GET  /api/doctor      → doctor_checks() rows
    GET  /api/audit       → 17-repo audit table rows
    POST /api/render      → {id} job start (body: render options)
    GET  /api/status?id=  → {state, progress, log, report}
    GET  /media?job=&name=→ file serve (whitelist + path-traversal safe)
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from video_creator import (  # noqa: E402
    VERSION,
    audit_rows,
    doctor_checks,
    find_ffmpeg,
    render_video,
)

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
JOBS_DIR = ROOT / "webjobs"
JOBS_DIR.mkdir(exist_ok=True)

PORT = int(os.environ.get("PORT", "8788"))
MAX_TOPIC_LEN = 120
MEDIA_ALLOWLIST = {
    "vaanireel_video.mp4", "vaanireel_video_clean.mp4", "base.mp4", "mixed.mp4",
    "report.json", "subs.srt",
    "card_1.png", "card_2.png", "card_3.png", "card_4.png", "card_5.png", "card_6.png",
}
CONTENT_TYPES = {
    ".mp4": "video/mp4", ".png": "image/png", ".json": "application/json",
    ".srt": "text/plain; charset=utf-8", ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".wav": "audio/wav", ".jpg": "image/jpeg",
}

# ---------------------------------------------------------------------------
# Job registry — ek waqt me ek render (pipeline ffmpeg-heavy hai, queue fair hai)
# ---------------------------------------------------------------------------
class JobRegistry:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.render_lock = threading.Lock()

    def start(self, options: dict) -> tuple[str | None, str | None]:
        if not self.render_lock.acquire(blocking=False):
            return None, "ek render pehle se chal raha hai — thodi der baad try karo"
        job_id = f"job_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        job = {
            "id": job_id, "dir": job_dir, "options": options,
            "state": "running", "progress": 0,
            "log": [], "report": None, "error": None, "started": time.time(),
        }
        with self.lock:
            self.jobs[job_id] = job

        log_lock = threading.Lock()

        def sink(line: str):
            with log_lock:
                job["log"].append(line)
                m = re.match(r"\[(\d)/6\]", line)
                if m:
                    job["progress"] = max(job["progress"], int(m.group(1)) * 15)

        def run():
            try:
                report = render_video(job["options"]["topic"], logger=sink,
                                      out_dir=job_dir, **{k: v for k, v in options.items()
                                                          if k != "topic"})
                job["report"] = report
                job["state"] = "done"
                job["progress"] = 100
            except Exception as e:  # noqa: BLE001 — job-level isolation
                job["state"] = "error"
                job["error"] = f"{type(e).__name__}: {e}"
                sink(f"[X] {job['error']}")
            finally:
                self.render_lock.release()

        threading.Thread(target=run, daemon=True, name=f"render-{job_id}").start()
        return job_id, None

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            return self.jobs.get(job_id)


REGISTRY = JobRegistry()


def _validate_options(body: dict) -> tuple[dict | None, str | None]:
    """Whitelist + clamp — koi direct passthrough nahi (security)."""
    if not isinstance(body, dict):
        return None, "body JSON object hona chahiye"

    topic = body.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        return None, "topic required hai"
    topic = topic.strip()[:MAX_TOPIC_LEN]

    lang = body.get("lang", "hi")
    lang = lang if lang in ("hi", "en") else "hi"

    try:
        scenes = int(body.get("scenes", 3))
    except (TypeError, ValueError):
        return None, "scenes number hona chahiye"
    scenes = max(3, min(6, scenes))

    visual = body.get("visual", "cards")
    visual = visual if visual in ("cards", "stock", "higgsfield") else "cards"

    size = body.get("size", "horizontal")
    size = size if size in ("horizontal", "vertical", "square") else "horizontal"

    engine = body.get("engine", "auto")
    engine = engine if engine in ("auto", "voicestudio", "edge", "piper", "silent") else "auto"

    return {
        "topic": topic, "lang": lang, "scenes_n": scenes, "visual": visual,
        "size": size, "engine": engine,
        "music": bool(body.get("music", False)),
        "captions": bool(body.get("captions", True)),
    }, None


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- helpers -----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _file(self, path: Path):
        if not path.exists() or not path.is_file():
            self._json({"error": "file not found"}, 404)
            return
        ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        rng = self.headers.get("Range")
        if rng and path.suffix == ".mp4":
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                start = int(m.group(1) or 0)
                end = int(m.group(2) or len(data) - 1)
                end = min(end, len(data) - 1)
                chunk = data[start:end + 1]
                self._send(206, chunk, ctype, {
                    "Content-Range": f"bytes {start}-{end}/{len(data)}",
                    "Accept-Ranges": "bytes",
                })
                return
        self._send(200, data, ctype, {"Accept-Ranges": "bytes"})

    def log_message(self, fmt, *args):  # quiet access log
        pass

    # -- routes ------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path

        if path == "/":
            self._file(WEB_DIR / "index.html")
        elif path == "/api/health":
            ff = find_ffmpeg()
            self._json({"ok": True, "tool": VERSION, "ffmpeg": bool(ff),
                        "ffmpeg_path": ff})
        elif path == "/api/doctor":
            self._json({"rows": doctor_checks()})
        elif path == "/api/audit":
            self._json({"rows": audit_rows()})
        elif path == "/api/status":
            job = REGISTRY.get(q.get("id", [""])[0])
            if not job:
                self._json({"error": "unknown job id"}, 404)
                return
            with REGISTRY.lock:
                payload = {
                    "id": job["id"], "state": job["state"], "progress": job["progress"],
                    "log": job["log"][-40:], "report": job["report"], "error": job["error"],
                    "dir": job["id"],
                }
            self._json(payload)
        elif path == "/media":
            job = REGISTRY.get(q.get("job", [""])[0])
            name = q.get("name", [""])[0]
            # path traversal guard: sirf allowlist + registry-known job dirs
            if not job or name not in MEDIA_ALLOWLIST or "/" in name or ".." in name \
                    or name != os.path.basename(name):
                self._json({"error": "forbidden"}, 400)
                return
            self._file(job["dir"] / name)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/render":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0") or 0), 64_000)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid JSON body"}, 400)
            return
        options, err = _validate_options(body)
        if err:
            self._json({"error": err}, 400)
            return
        job_id, err = REGISTRY.start(options)
        if err:
            self._json({"error": err}, 409)
            return
        self._json({"id": job_id})


def main() -> int:
    ff = find_ffmpeg()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"🎬 Vaanireel Web Studio v{VERSION}")
    print(f"   http://localhost:{PORT}   (bind 0.0.0.0:{PORT})")
    print(f"   ffmpeg: {'OK' if ff else 'MISSING — --doctor chalao'}")
    print("   Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye 👋")
    return 0


if __name__ == "__main__":
    sys.exit(main())
