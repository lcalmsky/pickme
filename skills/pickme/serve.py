#!/usr/bin/env python3
"""요청자 단계 페이지를 127.0.0.1 전용으로 열고, 제출을 받으면 그대로 끝난다."""
import argparse
import http.server
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

import config

HERE = Path(__file__).resolve().parent
MAX_BODY = 256 * 1024
DEFAULT_TIMEOUT = 3600
OPEN_MODES = ("auto", "orca", "browser", "none")
ORCA_TIMEOUT = 10


def fail(message):
    print("error: " + message, file=sys.stderr)
    sys.exit(2)


def executable(binary):
    if os.sep in binary:
        return os.path.isfile(binary) and os.access(binary, os.X_OK)
    return shutil.which(binary) is not None


def orca_binary():
    return os.environ.get("ORCA_BIN") or "orca"


def open_via_orca(url):
    binary = orca_binary()
    if not executable(binary):
        return False, "%s not found" % binary
    try:
        proc = subprocess.run([binary, "tab", "create", "--url", url],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=ORCA_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "timed out after %ds" % ORCA_TIMEOUT
    except OSError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        text = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        return False, (text.splitlines() or ["exit %d" % proc.returncode])[0]
    return True, None


def open_via_browser(url):
    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        return False, str(exc) or exc.__class__.__name__
    return (True, None) if opened else (False, "no browser available")


def open_line(mode, url):
    if mode == "auto":
        inside_orca = os.environ.get("TERM_PROGRAM") == "Orca" and executable(orca_binary())
        mode = "orca" if inside_orca else "browser"
    if mode == "none":
        return "opened: none"
    opened, reason = open_via_orca(url) if mode == "orca" else open_via_browser(url)
    return "opened: %s" % mode if opened else "open-failed: %s %s" % (mode, reason)


def resolve_open_mode(requested):
    """--open 값, 없으면 설정값, 그것도 없으면 auto. 깨진 설정은 (none, 사유) 로 알린다."""
    if requested:
        return requested, None
    try:
        stored = config.get("open")
    except config.ConfigError as exc:
        return "none", str(exc)
    return (stored, None) if stored in OPEN_MODES else ("auto", None)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def respond(self, code, body, ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def route(self):
        return self.path.split("?", 1)[0]

    def do_GET(self):
        if self.route() != "/%s/" % self.server.token:
            return self.respond(404, b"not found\n")
        try:
            body = self.server.page.read_bytes()
        except OSError:
            return self.respond(404, b"not found\n")
        self.respond(200, body, "text/html; charset=utf-8")

    def do_POST(self):
        if self.route() != "/%s/submit" % self.server.token:
            return self.respond(404, b"not found\n")
        # 다른 사이트가 브라우저를 통해 이 서버로 밀어 넣는 것을 막는다
        origin = self.headers.get("Origin")
        if origin and origin != "http://127.0.0.1:%d" % self.server.server_address[1]:
            return self.respond(403, b"forbidden\n")
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "text/plain":
            return self.respond(415, b"unsupported media type\n")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.respond(400, b"bad request\n")
        if length <= 0 or length > MAX_BODY:
            return self.respond(413, b"payload too large\n")
        body = self.rfile.read(length)
        reply = self.server.reply
        handle = None
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=str(reply.parent), prefix=".reply-")
            handle = os.fdopen(fd, "wb")
            handle.write(body)
            handle.close()
            handle = None
            os.replace(tmp_path, str(reply))
        except OSError as exc:
            if handle is not None:
                handle.close()
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            return self.respond(500, b"cannot write reply\n")
        self.respond(200, b'{"ok":true}', "application/json; charset=utf-8")
        # 응답을 다 내보낸 뒤에 알린다. 이 이벤트가 "제출 완료" 신호다
        print("submitted: %s" % reply, flush=True)
        self.server.done.set()


def main(argv):
    parser = argparse.ArgumentParser(prog="serve.py")
    parser.add_argument("--dir", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--open", choices=OPEN_MODES, default=None)
    args = parser.parse_args(argv)

    root = Path(args.dir).expanduser().resolve()
    if not root.is_dir():
        fail("no such directory: %s" % root)

    token = secrets.token_urlsafe(16)
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        fail("cannot bind 127.0.0.1:%d: %s" % (args.port, exc))
    server.token = token
    server.page = root / "page-local.html"
    server.reply = root / "requester-reply.txt"
    server.done = threading.Event()

    port = server.server_address[1]
    base = "http://127.0.0.1:%d/%s/" % (port, token)
    proc = subprocess.run(
        [sys.executable, str(HERE / "render.py"), "render", str(root / "decisions.json"),
         "--out", str(server.page), "--submit-url", base + "submit"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        server.server_close()
        fail("render failed: %s" % (proc.stderr.strip() or proc.stdout.strip()))

    print("serving: %s" % base, flush=True)

    deadline = time.monotonic() + args.timeout

    mode, config_error = resolve_open_mode(args.open)
    if config_error:
        print("open-failed: config %s" % config_error, flush=True)
    elif mode == "none":
        print("opened: none", flush=True)
    else:
        # $BROWSER 명령의 종료를 기다리는 열기가 서버 루프를 막지 않도록 데몬 스레드에 맡긴다
        threading.Thread(target=lambda: print(open_line(mode, base), flush=True),
                         daemon=True).start()
    try:
        while not server.done.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("timeout", flush=True)
                return 3
            server.timeout = min(remaining, 0.5)
            server.handle_request()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
