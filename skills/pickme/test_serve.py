import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 저장소 뿌리에서 `python3 -m unittest skills/pickme/test_serve.py` 로도 돌게 한다
sys.path.insert(0, str(HERE))

from test_render import REQUESTER_REPLY

SERVE = HERE / "serve.py"
RENDER = HERE / "render.py"
EXAMPLE = HERE / "example.json"

SERVING_RE = re.compile(r"^serving: (http://127\.0\.0\.1:(\d+)/([A-Za-z0-9_-]+)/)$")


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
    def __init__(self, timeout="30"):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(EXAMPLE, self.tmp / "decisions.json")
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVE), "--dir", str(self.tmp), "--timeout", timeout],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = self.proc.stdout.readline().strip()
        match = SERVING_RE.match(line)
        if not match:
            self.proc.kill()
            raise AssertionError("serve.py did not print a serving line: %r" % line)
        self.base, self.port, self.token = match.group(1), int(match.group(2)), match.group(3)
        self.submit_url = self.base + "submit"

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


if __name__ == "__main__":
    unittest.main()
