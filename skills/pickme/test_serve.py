import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 저장소 뿌리에서 `python3 -m unittest skills/pickme/test_serve.py` 로도 돌게 한다
sys.path.insert(0, str(HERE))

from test_render import REQUESTER_REPLY

import serve

SERVE = HERE / "serve.py"
RENDER = HERE / "render.py"
EXAMPLE = HERE / "example.json"

SERVING_RE = re.compile(r"^serving: (http://127\.0\.0\.1:(\d+)/([A-Za-z0-9_-]+)/)$")
OPEN_RESULT_RE = re.compile(r"^(opened: |open-failed: )")


def http(method, url, body=None, headers=None):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        exc.close()
        return exc.code, body


class ServeHarness:
    # --open none 이 기본이라 실제 브라우저나 Orca 탭이 열리지 않는다
    def __init__(self, timeout="30", args=("--open", "none"), env=None):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(EXAMPLE, self.tmp / "decisions.json")
        # 설정 파일이 없는 상태에서 띄운다. 이 컴퓨터의 설정이 테스트로 새지 않게 한다
        proc_env = dict(os.environ)
        proc_env["PICKME_CONFIG"] = str(self.tmp / "config.json")
        for key, value in (env or {}).items():
            if value is None:
                proc_env.pop(key, None)
            else:
                proc_env[key] = value
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVE), "--dir", str(self.tmp), "--timeout", timeout, *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=proc_env)
        line = self.proc.stdout.readline().strip()
        match = SERVING_RE.match(line)
        if not match:
            self.proc.kill()
            raise AssertionError("serve.py did not print a serving line: %r" % line)
        self.base, self.port, self.token = match.group(1), int(match.group(2)), match.group(3)
        self.submit_url = self.base + "submit"
        self._opened = None
        self.skipped = []

    def _readline(self, timeout):
        box = []
        reader = threading.Thread(target=lambda: box.append(self.proc.stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout)
        return box[0].rstrip("\n") if box else None

    def opened_line(self, timeout=10.0):
        """열기 결과 줄을 순서와 무관하게 찾는다. 열기가 느리면 나오지 않을 수 있다."""
        if self._opened is not None:
            return self._opened
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ""
            line = self._readline(remaining)
            if line is None:
                return ""
            if OPEN_RESULT_RE.match(line):
                self._opened = line
                return line
            self.skipped.append(line)

    @property
    def opened(self):
        return self.opened_line()

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=5)
        self.proc.stdout.close()
        self.proc.stderr.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.serve = ServeHarness()
        self.addCleanup(self.serve.close)

    def post(self, body=REQUESTER_REPLY, headers=None):
        base = {"Content-Type": "text/plain"}
        base.update(headers or {})
        return http("POST", self.serve.submit_url, body.encode("utf-8"), base)

    def test_wrong_token_is_not_found(self):
        status, _ = http("GET", "http://127.0.0.1:%d/%s/" % (self.serve.port, "nope-token"))
        self.assertEqual(status, 404)

    def test_page_carries_the_submit_url(self):
        status, text = http("GET", self.serve.base)
        self.assertEqual(status, 200)
        self.assertIn('text: t("submitButton")', text)
        payload = json.loads(re.search(
            r'<script type="application/json" id="apq-payload">(.*?)</script>', text, re.S).group(1))
        self.assertEqual(payload["submit_url"], self.serve.submit_url)
        self.assertEqual(payload["phase"], "requester")

    def test_foreign_origin_is_rejected_and_server_survives(self):
        status, _ = self.post(headers={"Origin": "https://example.com"})
        self.assertEqual(status, 403)
        self.assertIsNone(self.serve.proc.poll(), "400 응답 뒤에도 서버는 계속 살아 있어야 한다")
        self.assertEqual(http("GET", self.serve.base)[0], 200)

    def test_submit_writes_the_reply_and_exits_zero(self):
        status, body = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True})
        self.assertEqual((self.serve.tmp / "requester-reply.txt").read_text(encoding="utf-8"),
                         REQUESTER_REPLY)
        rest = self.serve.proc.stdout.read()
        self.assertEqual(self.serve.proc.wait(timeout=5), 0)
        self.assertIn("submitted: %s" % (self.serve.tmp.resolve() / "requester-reply.txt"), rest)

    def test_submitted_reply_applies(self):
        self.assertEqual(self.post()[0], 200)
        self.serve.proc.wait(timeout=5)
        proc = subprocess.run(
            [sys.executable, str(RENDER), "apply", str(self.serve.tmp / "decisions.json"),
             str(self.serve.tmp / "requester-reply.txt")],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("applied: requester picks=4", proc.stdout)
        data = json.loads((self.serve.tmp / "decisions.json").read_text(encoding="utf-8"))
        self.assertEqual(data["requester_picks"]["d1"], {"c": "b"})

    def test_non_text_plain_body_is_rejected(self):
        status, _ = self.post(headers={"Content-Type": "application/json"})
        self.assertEqual(status, 415)

    def test_timeout_exits_three(self):
        self.serve.close()
        self.serve = ServeHarness(timeout="1")
        self.addCleanup(self.serve.close)
        self.assertEqual(self.serve.proc.wait(timeout=10), 3)
        self.assertEqual(self.serve.proc.stdout.read().strip().splitlines()[-1], "timeout")

    def test_open_none_opens_nothing(self):
        self.assertEqual(self.serve.opened, "opened: none")


BROWSER_BODY = 'printf "%s\\n" "$1" >> "$BROWSER_LOG"\n'
ORCA_BODY = 'printf "%s\\n" "$*" >> "$CALL_LOG"\n'


class OpenTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.log = self.scratch / "calls.log"

    def start(self, args, env):
        serve = ServeHarness(args=args, env=env)
        self.addCleanup(serve.close)
        return serve

    def script(self, name, body):
        path = self.scratch / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def calls(self):
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def browser_env(self):
        script = self.script("browser.sh", BROWSER_BODY)
        return {"BROWSER": "%s %s" % (script, "%s"), "BROWSER_LOG": str(self.log)}

    def orca_env(self):
        script = self.script("orca.sh", ORCA_BODY)
        return {"ORCA_BIN": str(script), "CALL_LOG": str(self.log)}

    def test_open_browser_records_the_serving_url(self):
        serve = self.start(("--open", "browser"), self.browser_env())
        self.assertEqual(serve.opened, "opened: browser")
        self.assertEqual(self.calls(), [serve.base])

    def test_open_orca_runs_tab_create(self):
        serve = self.start(("--open", "orca"), self.orca_env())
        self.assertEqual(serve.opened, "opened: orca")
        self.assertEqual(self.calls(), ["tab create --url %s" % serve.base])

    def test_missing_orca_binary_is_reported_and_the_server_keeps_serving(self):
        serve = self.start(("--open", "orca"), {"ORCA_BIN": str(self.scratch / "no-such-orca")})
        self.assertTrue(serve.opened.startswith("open-failed: orca "), serve.opened)
        self.assertEqual(http("GET", serve.base)[0], 200)

    def test_open_mode_comes_from_the_config_file(self):
        config = self.scratch / "config.json"
        config.write_text('{"open": "none"}', encoding="utf-8")
        serve = self.start((), {"PICKME_CONFIG": str(config)})
        self.assertEqual(serve.opened, "opened: none")

    def test_broken_config_is_reported_and_the_server_keeps_serving(self):
        config = self.scratch / "config.json"
        config.write_text("{not json", encoding="utf-8")
        serve = self.start((), {"PICKME_CONFIG": str(config)})
        self.assertTrue(serve.opened.startswith("open-failed: config "), serve.opened)
        self.assertEqual(http("GET", serve.base)[0], 200)

    def test_auto_inside_orca_opens_an_orca_tab(self):
        env = self.orca_env()
        env["TERM_PROGRAM"] = "Orca"
        serve = self.start(("--open", "auto"), env)
        self.assertEqual(serve.opened, "opened: orca")
        self.assertEqual(self.calls(), ["tab create --url %s" % serve.base])

    def test_auto_outside_orca_opens_the_browser(self):
        env = self.browser_env()
        env["TERM_PROGRAM"] = None
        serve = self.start(("--open", "auto"), env)
        self.assertEqual(serve.opened, "opened: browser")
        self.assertEqual(self.calls(), [serve.base])

    def test_auto_falls_back_to_the_browser_without_orca(self):
        env = self.browser_env()
        env["TERM_PROGRAM"] = "Orca"
        env["ORCA_BIN"] = str(self.scratch / "no-such-orca")
        serve = self.start(("--open", "auto"), env)
        self.assertEqual(serve.opened, "opened: browser")
        self.assertEqual(self.calls(), [serve.base])


class SlowBrowserTests(unittest.TestCase):
    # $BROWSER 명령이 끝나기를 기다리는 열기가 서버 루프를 막지 않는지 본다
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.log = self.scratch / "calls.log"
        script = self.scratch / "slow-browser.sh"
        script.write_text(
            '#!/bin/sh\nsleep 5\nprintf "%%s\\n" "$1" >> "$BROWSER_LOG"\n', encoding="utf-8")
        script.chmod(0o755)
        self.env = {"BROWSER": "%s %s" % (script, "%s"), "BROWSER_LOG": str(self.log)}

    def start(self, timeout):
        serve = ServeHarness(timeout=timeout, args=("--open", "browser"), env=self.env)
        self.addCleanup(serve.close)
        return serve

    def test_server_serves_and_takes_a_submit_while_the_browser_is_slow(self):
        serve = self.start("30")
        started = time.monotonic()
        self.assertEqual(http("GET", serve.base)[0], 200)
        self.assertLess(time.monotonic() - started, 5.0, "느린 열기가 GET 을 막았다")
        status, _ = http("POST", serve.submit_url, REQUESTER_REPLY.encode("utf-8"),
                         {"Content-Type": "text/plain"})
        self.assertEqual(status, 200)
        self.assertEqual(serve.proc.wait(timeout=10), 0)

    def test_timeout_is_not_delayed_by_a_slow_browser(self):
        serve = self.start("2")
        started = time.monotonic()
        self.assertEqual(serve.proc.wait(timeout=10), 3)
        self.assertLess(time.monotonic() - started, 4.0, "느린 열기가 --timeout 을 밀었다")
        self.assertEqual(serve.proc.stdout.read().strip().splitlines()[-1], "timeout")


class BrowserUnitTests(unittest.TestCase):
    # webbrowser.open 이 False 인 경우는 실제 브라우저로 흘러가므로 프로세스로 시험하지 않는다
    def test_false_from_webbrowser_is_a_failure(self):
        original = serve.webbrowser.open
        serve.webbrowser.open = lambda url: False
        self.addCleanup(setattr, serve.webbrowser, "open", original)
        self.assertEqual(serve.open_via_browser("http://127.0.0.1:1/tok/"),
                         (False, "no browser available"))
        self.assertTrue(serve.open_line("browser", "http://127.0.0.1:1/tok/")
                        .startswith("open-failed: browser "))


if __name__ == "__main__":
    unittest.main()
