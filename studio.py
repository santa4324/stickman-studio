#!/usr/bin/env python3
"""Local studio for stickman explainers.

Open http://localhost:8765, paste a script, get a narrated 9:16 film back.
Nothing leaves the machine: Piper narrates, the installed B1 runtime renders.

    python3 studio.py            # then open the URL it prints
    python3 studio.py --port 9000
"""

import argparse
import json
import queue
import re
import threading
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import stickman

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "out" / "studio"
JOBS = {}
LOCK = threading.Lock()
QUEUE: "queue.Queue[str]" = queue.Queue()


def slug(text, limit=40):
    words = re.sub(r"[^a-z0-9\\s-]", "", text.lower()).split()
    return ("-".join(words[:6]) or "film")[:limit]


def worker():
    while True:
        job_id = QUEUE.get()
        job = JOBS[job_id]
        try:
            outdir = RUNS / job_id
            job["status"] = "running"

            def progress(message, job=job):
                job["stage"] = message

            result = stickman.build(job["script"], outdir, on_progress=progress)
            job.update(status="done", stage="finished", result=result,
                       film=f"/files/{job_id}/film.mp4",
                       srt=f"/files/{job_id}/film.srt",
                       outdir=str(outdir))
        except Exception as error:  # surfaced in the UI, not swallowed
            job.update(status="failed", error=str(error), stage="failed")
        finally:
            QUEUE.task_done()


HTML = (ROOT / "studio.html").read_text()


class Handler(BaseHTTPRequestHandler):
    server_version = "StickmanStudio/1.0"

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, HTML.encode(), "text/html; charset=utf-8")
            return
        if self.path == "/api/jobs":
            with LOCK:
                recent = sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)[:12]
            self._send(200, json.dumps(recent).encode())
            return
        match = re.match(r"^/api/jobs/([0-9a-f]+)$", self.path)
        if match:
            job = JOBS.get(match.group(1))
            self._send(200, json.dumps(job).encode()) if job else self._send(404, b"{}")
            return
        match = re.match(r"^/files/([0-9a-f]+)/(film\.(mp4|srt))$", self.path)
        if match:
            self._serve_file(RUNS / match.group(1) / match.group(2))
            return
        self._send(404, b"not found", "text/plain")

    def _serve_file(self, path):
        if not path.exists():
            self._send(404, b"not found", "text/plain")
            return
        data = path.read_bytes()
        ctype = "video/mp4" if path.suffix == ".mp4" else "text/plain; charset=utf-8"
        self._send(200, data, ctype, {"Cache-Control": "no-store"})

    def do_POST(self):
        if self.path != "/api/render":
            self._send(404, b"{}")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, json.dumps({"error": "bad JSON"}).encode())
            return

        script = (payload.get("script") or "").strip()
        if len(script) < 20:
            self._send(400, json.dumps({"error": "Write at least one full sentence."}).encode())
            return
        if len(script) > 6000:
            self._send(400, json.dumps({"error": "Keep the script under 6000 characters."}).encode())
            return

        job_id = uuid.uuid4().hex[:12]
        RUNS.mkdir(parents=True, exist_ok=True)
        job = {
            "id": job_id,
            "script": script,
            "created": datetime.now().isoformat(timespec="seconds"),
            "status": "queued",
            "stage": "waiting for the renderer",
            "title": " ".join(script.split())[:60],
        }
        with LOCK:
            JOBS[job_id] = job
        QUEUE.put(job_id)
        self._send(202, json.dumps(job).encode())


def main():
    parser = argparse.ArgumentParser(description="Local stickman explainer studio.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    threading.Thread(target=worker, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Stickman studio running at {url}")
    print("Paste a script in the page and press Render. Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
