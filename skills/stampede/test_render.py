import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDER = HERE / "render.py"
EXAMPLE = HERE / "example.json"
# 화면 문구 사전은 사전 밖으로 새어 나간 문구를 가리기 위해 검사에서 걷어낸다
I18N_RE = re.compile(r"var I18N =.*?\n  \};", re.S)
EN_EXAMPLE = HERE.parent.parent / "docs" / "demos" / "en" / "requester.json"

PAYLOAD_RE = re.compile(
    r'<script type="application/json" id="apq-payload">(.*?)</script>', re.S)

CUSTOM_REQUESTER_RE = re.compile(
    r'custom\s*&&\s*requesterPick\s*&&\s*requesterPick\.c\s*===\s*"x"')

# 직접 입력 칸은 값 대신 "직접" 표지만 두고, 값은 비교 행 아래 .anote 줄로 내려 적는다
CUSTOM_CELL_RE = re.compile(
    r'class:\s*"ctext",\s*text:\s*pick\.c\s*===\s*"x"\s*\?\s*t\("customShort"\)\s*:\s*letterOf\(pick\.c\)')

ANOTE_RE = re.compile(
    r'class:\s*"anote"\s*\},\s*\[\s*\n\s*el\("span",\s*\{\s*text:\s*line\.head')

# 결정권자 칸은 part 마다 요청자 일치를 자기 색과 모서리 기호로 직접 보여 준다
CELL_MARK_CSS_RE = re.compile(
    r'\.cell\.stamp\.same\s*\{[^}]*--ai[^}]*\}.*?'
    r'\.cell\.stamp\.memo\s*\{[^}]*--req[^}]*\}.*?'
    r'\.cell\.stamp\.diff\s*\{[^}]*--stamp[^}]*\}', re.S)

PICK_MARK_RE = re.compile(
    r'function\s+pickMark\(rp,\s*mine,\s*memoed\)\s*\{\s*\n'
    r'\s*if\s*\(!rp\)\s*return\s+null;\s*\n'
    r'\s*if\s*\(!samePick\(rp,\s*mine\)\)\s*return\s*\{\s*cls:\s*"diff".*?\n'
    r'\s*if\s*\(memoed\)\s*return\s*\{\s*cls:\s*"memo".*?\n'
    r'\s*return\s*\{\s*cls:\s*"same"')

# 미리보기는 가로 스크롤 대신 줄바꿈해서 전부 보여 준다
SUMMARY_CSS_RE = re.compile(r'#apq-summary\s*\{([^}]*)\}')

# 완료 강조 애니메이션은 CSS 애니메이션이어야 이 규칙이 실제로 막는다
REDUCED_MOTION_RE = re.compile(
    r'@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{\s*\*\s*\{([^}]*)\}')

DECRYPT_JS = """
import { webcrypto as crypto } from "node:crypto";
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const blob = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const pin = process.argv[2];
const keyMaterial = await crypto.subtle.importKey("raw", new TextEncoder().encode(pin), "PBKDF2", false, ["deriveKey"]);
const key = await crypto.subtle.deriveKey(
  { name: "PBKDF2", salt: Buffer.from(blob.salt, "base64"), iterations: blob.iterations, hash: "SHA-256" },
  keyMaterial, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
const pt = await crypto.subtle.decrypt(
  { name: "AES-GCM", iv: Buffer.from(blob.iv, "base64") }, key, Buffer.from(blob.ct, "base64"));
process.stdout.write(Buffer.from(pt).toString("utf8"));
"""

REQUESTER_REPLY = """[DEMO-12 회원가입 개편 결정 요청자 선택] 2026-10-11
1. 소셜 로그인을 첫 화면에 두는가 - 소셜 로그인을 위에 둔다 [AI 권장과 같음]
   메모: 첫 화면에서 이탈이 많습니다
2. 약관 동의를 어느 화면에 모으는가 - 가입 화면 하나에 모은다 [AI 권장과 같음]
3. 가입 완료 문구와 실패 시 되돌리기 - 완료 문구: 가입을 마쳤어요 [AI 권장과 같음] / 실패 시 되돌리기: 남겨 두고 다시 시도하게 한다 [AI 권장과 같음]
전체 메모: 전체 메모입니다
선택 코드: apq|demo-signup-revamp|v1|requester|d1=b;d2=a;d3=a;d3r=b
"""

# phase 를 decider 로 바꾸기 전에 공유된 페이지가 보내는 형식
PO_REPLY = """[DEMO-12 회원가입 개편 결정 디자이너 선택] 2026-10-11
요청자 선택과 다른 선택: 1개
1. 소셜 로그인을 첫 화면에 두는가 - 이메일 가입만 둔다 [다름, 요청자: 소셜 로그인을 위에 둔다]
2. 약관 동의를 어느 화면에 모으는가 - 가입 화면 하나에 모은다 [요청자와 같음]
3. 가입 완료 문구와 실패 시 되돌리기 - 완료 문구: 가입을 마쳤어요 [요청자와 같음] / 실패 시 되돌리기: 남겨 두고 다시 시도하게 한다 [요청자와 같음]
   메모: 완료 문구는 이대로 갑니다
선택 코드: apq|demo-signup-revamp|v1|po|d1=a;d2=a;d3=a;d3r=b
"""

DECIDER_REPLY = PO_REPLY.replace("v1|po|", "v1|decider|")

CUSTOM_REPLY = """[DEMO-12 회원가입 개편 결정 요청자 선택] 2026-10-11
1. 소셜 로그인을 첫 화면에 두는가 - 소셜 로그인을 위에 둔다 [AI 권장과 같음]
2. 약관 동의를 어느 화면에 모으는가 - 이메일 주소로 다시 받기 [다름, AI 권장: 가입 화면 하나에 모은다]
3. 가입 완료 문구와 실패 시 되돌리기 - 완료 문구: 가입을 마쳤어요 [AI 권장과 같음] / 실패 시 되돌리기: 남겨 두고 다시 시도하게 한다 [AI 권장과 같음]
선택 코드: apq|demo-signup-revamp|v1|requester|d1=b;d2=x(이메일 주소로 다시 받기);d3=a;d3r=b
"""

# 영어 페이지가 내는 복사 텍스트. render.py 의 머리말 정규식이 두 언어를 모두 받아야 한다
EN_REPLY = """[DEMO-12 signup revamp decisions requester pick] 2026-10-11
1. Should social login sit on the first screen - Put social login first [Same as the AI recommendation]
   Memo: keep it above the fold
2. Which screen collects the consent items - Ask for the email again [Different, AI recommendation: Collect them on the signup screen]
3. Wording when signup finishes, and what a failure rolls back - Finish wording: You are all set [Same as the AI recommendation] / Failure rollback: Keep it and let the person retry [Same as the AI recommendation]
Overall memo: Ship it this week
Selection code: apq|demo-signup-revamp|v1|requester|d1=b;d2=x(Ask for the email again);d3=a;d3r=b
"""


def run_render(*args):
    return subprocess.run([sys.executable, str(RENDER), *args],
                          capture_output=True, text=True)


def payload_of(html_path):
    text = Path(html_path).read_text(encoding="utf-8")
    match = PAYLOAD_RE.search(text)
    if not match:
        raise AssertionError("payload script not found in %s" % html_path)
    return json.loads(match.group(1)), text


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, name, text):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_requester_phase_marker(self):
        out = self.tmp / "requester.html"
        proc = run_render("render", str(EXAMPLE), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("phase=requester", proc.stdout)
        self.assertNotIn("pin:", proc.stdout)
        payload, text = payload_of(out)
        self.assertIs(payload["locked"], False)
        self.assertEqual(payload["phase"], "requester")
        self.assertIn("소셜 로그인을 첫 화면에 두는가", text)
        self.assertIn("<title>DEMO-12 회원가입 개편 결정</title>", text)
        self.assertIn('"requester"', text)

    def test_decider_phase_marker(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["requester_picks"] = {"d1": {"c": "a"}, "d2": {"c": "x", "x": "입력칸 아래"}}
        data["requester_memos"] = {"d1": "메모"}
        path = self.write("with-picks.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "decider.html"
        proc = run_render("render", str(path), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("phase=decider", proc.stdout)
        payload, text = payload_of(out)
        self.assertEqual(payload["phase"], "decider")
        self.assertIn('tf("requesterDiffSummary"', text)

    def test_skip_requester_renders_decider_phase(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["skip_requester"] = True
        path = self.write("skip-requester.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "skip.html"
        proc = run_render("render", str(path), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("phase=decider", proc.stdout)
        payload, text = payload_of(out)
        self.assertEqual(payload["phase"], "decider")
        self.assertNotIn("requester_picks", payload["data"])

    def test_decider_page_keeps_requester_picks_as_reference_only(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["requester_picks"] = {"d1": {"c": "a"}, "d2": {"c": "x", "x": "입력칸 아래"}}
        path = self.write("with-picks.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "decider.html"
        proc = run_render("render", str(path), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        # 요청자 선택은 배지·비교 표시용 자료로만 남고, 결정권자 선택 상태를 미리 채우는 데 쓰이지 않는다
        self.assertEqual(payload["data"]["requester_picks"],
                         {"d1": {"c": "a"}, "d2": {"c": "x", "x": "입력칸 아래"}})
        self.assertNotIn("state.picks[id] = { c: p.c }", text)

    def test_custom_pick_reaches_the_decider_page(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["requester_picks"] = {"d2": {"c": "x", "x": "2026-10-14"}}
        path = self.write("custom-pick.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "custom.html"
        proc = run_render("render", str(path), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        self.assertEqual(payload["data"]["requester_picks"]["d2"], {"c": "x", "x": "2026-10-14"})
        self.assertRegex(text, CUSTOM_REQUESTER_RE,
                         "custom option must carry the requester badge branch")
        self.assertIn('t("requesterInputPrefix")', text)

    def test_custom_cell_keeps_a_marker_and_the_value_moves_to_a_note_line(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["requester_picks"] = {"d2": {"c": "x", "x": "2026-10-14 출시"}}
        path = self.write("custom-note.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "custom-note.html"
        proc = run_render("render", str(path), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        self.assertEqual(payload["data"]["requester_picks"]["d2"], {"c": "x", "x": "2026-10-14 출시"})
        self.assertRegex(text, CUSTOM_CELL_RE,
                         "custom cell must show the fixed mark instead of the value")
        self.assertRegex(text, ANOTE_RE,
                         "custom values must be repeated in a note line under the approval rows")
        self.assertIn('var head = tf("customNoteHead", { label: col.label });', text)
        self.assertIn('if (multi) head += " · " + (part.label || part.id);', text)
        self.assertIn('cols.push({ label: t("colRequester"), pick: rp });', text)
        self.assertIn('cols.push({ label: t("colMyPick"), pick: mine });', text)
        # 메모 줄은 .cside 밖 카드 본문 폭에 놓인다
        self.assertIn('var anotes = el("div", { class: "anotes", hidden: true });', text)
        self.assertIn("entry.anotes.hidden = !lines.length;", text)
        self.assertIn(".anote {", text)

    def test_decider_cell_marks_requester_agreement_per_part(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["requester_picks"] = {"d1": {"c": "a"}, "d2": {"c": "a"}, "d3": {"c": "a"}, "d3r": {"c": "b"}}
        path = self.write("match-color.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "match-color.html"
        proc = run_render("render", str(path), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        self.assertEqual(payload["phase"], "decider")
        self.assertRegex(text, CELL_MARK_CSS_RE,
                         "each agreement state needs its own cell.stamp color class")
        self.assertRegex(text, PICK_MARK_RE,
                         "the cell mark must be chosen from samePick and the decision memo")
        self.assertIn('var memoed = !!(state.memos[dec.id] && String(state.memos[dec.id]).trim());', text)
        self.assertIn('class: mark ? "acol marked" : "acol"', text)
        # 상태 색은 칸 자체에 붙어야 한다. 래퍼에만 붙으면 칸 테두리는 늘 빨강으로 남는다
        self.assertIn("cell.classList.add(mark.cls);", text)
        self.assertIn("function markBadge(mark) {", text)
        self.assertIn(".cmark {", text)
        # 모서리 기호는 .cell 밖 래퍼에 붙는다(.cell 은 overflow: hidden)
        self.assertIn(".acol.marked { position: relative; }", text)
        # 범례는 결정권자 단계에서만 기호 뜻을 한 줄 더한다
        self.assertIn('[["same", t("markLegendSame")], ["memo", t("markLegendMemo")], ["diff", t("markLegendDiff")]]', text)
        self.assertIn('if (phase === "decider" && requesterPicks) {\n      var markLegend', text)
        # 결정 단위 칩은 part 가 둘 이상일 때 다른 part 수를 함께 적는다
        self.assertIn('text: t("diffChip") + (multi ? " " + diffCount : "")', text)

    def test_locked_page_hides_plaintext(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        out = self.tmp / "locked.html"
        proc = run_render("render", str(EXAMPLE), "--out", str(out), "--pin")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        pin_line = [line for line in proc.stdout.splitlines() if line.startswith("pin: ")]
        self.assertEqual(len(pin_line), 1, proc.stdout)
        pin = pin_line[0][len("pin: "):]
        self.assertRegex(pin, r"^[0-9]{4}$")

        payload, text = payload_of(out)
        self.assertIs(payload["locked"], True)
        self.assertNotIn("data", payload)
        self.assertIn("salt", payload)
        self.assertIn("iv", payload)
        self.assertIn("ct", payload)
        self.assertIn("<title>디자이너 결정 요청</title>", text)
        # 잠긴 페이지의 잠금 화면은 복호화 전에 그려지므로 역할이 payload 로 실려 와야 한다
        self.assertEqual(payload["title"], "디자이너 결정 요청")

        secrets = [data["meta"]["title"]]
        secrets.extend(data["meta"]["intro"])
        for decision in data["decisions"]:
            secrets.append(decision["title"])
            secrets.append(decision["question"])
            secrets.extend(decision.get("background") or [])
            for part in decision["parts"]:
                secrets.extend(opt["label"] for opt in part["options"])
        for secret in secrets:
            self.assertNotIn(secret, text, "plaintext leaked: %s" % secret[:40])

    def test_pin_value_is_used(self):
        out = self.tmp / "locked.html"
        proc = run_render("render", str(EXAMPLE), "--out", str(out), "--pin", "--pin-value", "0421")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("pin: 0421", proc.stdout)

    def test_pin_value_must_be_four_digits(self):
        out = self.tmp / "locked.html"
        proc = run_render("render", str(EXAMPLE), "--out", str(out), "--pin", "--pin-value", "42")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("error:", proc.stderr)

    def test_decider_role_shapes_the_page_text(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["meta"].pop("title")
        data["meta"]["decider_role"] = "디자이너"
        data["meta"]["decider"] = "하윤"
        data["requester_picks"] = {"d1": {"c": "a"}}
        path = self.write("designer.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "designer.html"
        proc = run_render("render", str(path), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        self.assertEqual(payload["phase"], "decider")
        self.assertIn("<title>디자이너 결정 요청</title>", text)
        self.assertIn('tf("docTitle", { role: role })', text)
        self.assertIn("role + ' ' + deciderName(meta)", text)
        self.assertIn('tf("headerDecider", { title: meta.title || "", role: role, created: meta.created || "" })', text)
        self.assertIn('(requesterPicks ? [t("colRequester"), t("colAi"), role] : [t("colAi"), role])', text)
        self.assertIn('[tf("stepDeciderPick", { role: role }), t("stepHandoff")]', text)
        self.assertIn('text: tf("stepDeciderPick", { role: role })', text)
        # 화면 문구는 결정권자 역할로만 만들어지므로 "PO" 가 남지 않는다
        self.assertNotIn('" PO 선택] "', text)
        self.assertNotIn('"PO " + meta.po', text)
        self.assertNotIn("[PO ", text)
        self.assertNotIn("PO 선택", text)

    def test_legacy_meta_po_renders_as_the_po_role(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        meta = data["meta"]
        meta.pop("title")
        meta.pop("decider_role")
        meta.pop("decider")
        meta["po"] = "하윤"
        path = self.write("legacy.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "legacy.html"
        proc = run_render("render", str(path), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        _, text = payload_of(out)
        self.assertIn("<title>PO 결정 요청</title>", text)

    def test_locked_page_title_carries_the_role(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["meta"]["decider_role"] = "디자이너"
        data["meta"]["decider"] = "하윤"
        path = self.write("designer.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "designer-locked.html"
        proc = run_render("render", str(path), "--out", str(out), "--pin-value", "1234")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        self.assertIn("<title>디자이너 결정 요청</title>", text)
        self.assertEqual(payload["title"], "디자이너 결정 요청")
        # 잠금 화면도 같은 제목을 쓴다
        self.assertIn('el("h1", { text: blob.title || t("lockTitleFallback") })', text)

    def test_english_page_keeps_every_screen_string_in_the_dictionary(self):
        out = self.tmp / "en.html"
        proc = run_render("render", str(EN_EXAMPLE), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        self.assertEqual(payload["lang"], "en")
        self.assertIn('<html lang="en">', text)
        # 사전과 payload 를 걷어낸 나머지에 한국어가 남아 있으면 문구가 사전 밖에 있는 것이다
        rest = I18N_RE.sub("", PAYLOAD_RE.sub("", text))
        rest = re.sub(r"<style>.*?</style>", "", rest, flags=re.S)
        rest = re.sub(r"/\*.*?\*/", "", rest, flags=re.S)
        rest = re.sub(r"^\s*//.*$", "", rest, flags=re.M)
        korean = [line.strip() for line in rest.splitlines()
                  if any("가" <= char <= "힣" for char in line)]
        self.assertEqual(korean, [], "화면 문구가 사전 밖에 남아 있다")

    def test_language_reaches_the_locked_page_too(self):
        data = json.loads(EN_EXAMPLE.read_text(encoding="utf-8"))
        path = self.write("en-locked.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "en-locked.html"
        proc = run_render("render", str(path), "--out", str(out), "--pin-value", "1234")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        self.assertEqual(payload["lang"], "en")
        self.assertEqual(payload["title"], "Decision request for Designer")
        self.assertIn("<title>Decision request for Designer</title>", text)

    def test_korean_page_keeps_the_default_role_and_title(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["meta"].pop("title")
        data["meta"].pop("decider_role")
        data["meta"].pop("decider")
        path = self.write("default-role.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "default-role.html"
        proc = run_render("render", str(path), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        self.assertEqual(payload["lang"], "ko")
        self.assertIn("<title>결정권자 결정 요청</title>", text)

    def test_origin_doc_and_origin_label_reach_the_chip(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["decisions"][0]["origin"] = "doc"
        data["decisions"][0]["origin_label"] = "디자인 명세"
        path = self.write("doc-origin.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "doc-origin.html"
        proc = run_render("render", str(path), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        self.assertEqual(payload["data"]["decisions"][0]["origin_label"], "디자인 명세")
        self.assertIn("디자인 명세", text)
        anchor = text.index('var origin = dec.origin === "jira"')
        self.assertIn('dec.origin === "doc" ? t("originDoc")', text[anchor:text.index("\n", anchor)])
        self.assertIn('text: dec.origin_label || origin', text[anchor:text.index("\n", anchor) + 200])

    def test_copy_blocks_reach_the_page(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["decisions"][0]["copy_blocks"] = [
            {"label": "요청 글 초안", "text": "첫 줄입니다\n둘째 줄입니다"},
        ]
        path = self.write("with-copy-blocks.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "copy.html"
        proc = run_render("render", str(path), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        blocks = payload["data"]["decisions"][0]["copy_blocks"]
        self.assertEqual(blocks[0]["label"], "요청 글 초안")
        self.assertEqual(blocks[0]["text"], "첫 줄입니다\n둘째 줄입니다")
        self.assertIn("요청 글 초안", text)

    def test_copy_flow_keeps_one_copy_button_in_the_action_bar(self):
        out = self.tmp / "copy-flow.html"
        proc = run_render("render", str(EXAMPLE), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        _, text = payload_of(out)
        # "결과 복사" 버튼은 하단 고정 바 하나뿐이다. 요약 절의 인라인 버튼은 없앴다
        self.assertEqual(len(re.findall(r'text:\s*t\("copyResult"\)', text)), 1)
        self.assertIn('var abBtn = el("button", { type: "button", class: "btn", text: t("copyResult") });', text)
        self.assertNotIn('class: "actions"', text)
        self.assertNotIn("copyBtn", text)

        # 미리보기는 줄바꿈해서 전부 보이고, 위에 라벨이 붙는다
        match = SUMMARY_CSS_RE.search(text)
        self.assertIsNotNone(match, "#apq-summary rule not found")
        css = match.group(1)
        self.assertIn("white-space: pre-wrap", css)
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertNotIn("overflow-x", css)
        self.assertIn('el("div", { class: "part-label", text: t("copyTargetHead") })', text)

    def test_action_bar_reports_the_completion_state(self):
        out = self.tmp / "copy-flow.html"
        proc = run_render("render", str(EXAMPLE), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        _, text = payload_of(out)
        self.assertIn('abCount.textContent = complete ? t("completeBar") : tf("progressSelected", { total: total, done: done });', text)
        self.assertIn('abCount.classList.toggle("ready", complete);', text)
        self.assertIn(".ab-count.ready {", text)
        ready = re.search(r'\.ab-count\.ready\s*\{([^}]*)\}', text)
        self.assertIn("var(--ai)", ready.group(1), "완료 문구는 기존 토큰으로 강조한다")
        # 미선택이 남았을 때와 다 골랐을 때의 버튼 강조가 갈린다
        self.assertIn('abBtn.classList.toggle("primary", complete);', text)
        # 완료로 바뀌는 순간에만 한 번 알린다
        self.assertIn("var completeSeen = null;", text)
        self.assertIn("if (completeSeen === false && complete) {", text)
        self.assertIn('toast(t("toastAllPicked"));', text)
        self.assertRegex(text, r'\.actionbar\.pulse\s+\.btn\s*\{[^}]*animation:\s*abpulse')
        self.assertRegex(text, r'@keyframes abpulse\s*\{')
        motion = REDUCED_MOTION_RE.search(text)
        self.assertIsNotNone(motion, "prefers-reduced-motion rule not found")
        self.assertIn("animation: none !important", motion.group(1))

    def test_copy_toast_names_the_next_step_per_phase(self):
        out = self.tmp / "copy-flow.html"
        proc = run_render("render", str(EXAMPLE), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        self.assertEqual(payload["phase"], "requester")
        self.assertIn('toast(phase === "requester" ? t("toastCopiedRequester") : t("toastCopiedDecider"));', text)
        self.assertIn('abBtn.textContent = t("copiedCheck");', text)
        self.assertIn('abRevert = setTimeout(function () { abBtn.textContent = t("copyResult"); }, 2000);', text)
        self.assertIn('toast(t("toastCopyManual"));', text)

    def test_submit_url_reaches_the_payload_and_the_bar(self):
        out = self.tmp / "local.html"
        proc = run_render("render", str(EXAMPLE), "--out", str(out),
                          "--submit-url", "http://127.0.0.1:8123/tok/submit")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        self.assertEqual(payload["submit_url"], "http://127.0.0.1:8123/tok/submit")
        # 주 버튼이 전송이 되어도 복사 버튼은 하나 남아 폴백으로 쓰인다
        self.assertEqual(len(re.findall(r'text:\s*t\("copyResult"\)', text)), 1)
        self.assertEqual(len(re.findall(r'text:\s*t\("submitButton"\)', text)), 1)
        self.assertIn('class: "btn primary", text: t("submitButton")', text)
        self.assertIn("fetch(submitUrl, {", text)
        self.assertIn('headers: { "Content-Type": "text/plain" }', text)
        self.assertIn('body: buildText(),', text)
        self.assertIn('toast(t("toastSubmitFailed"));', text)
        self.assertIn('abCount.textContent = t("submittedMsg");', text)
        self.assertIn("abSubmit.disabled = true;", text)
        self.assertIn("abBtn.disabled = true;", text)

    def test_submit_url_is_absent_without_the_flag(self):
        out = self.tmp / "plain.html"
        proc = run_render("render", str(EXAMPLE), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, text = payload_of(out)
        # 버튼은 submitMode 일 때만 만들어진다. 페이지 소스가 아니라 payload 가 기준이다
        self.assertNotIn("submit_url", payload)
        self.assertIn('var submitMode = phase === "requester" && !!submitUrl;', text)
        self.assertIn('payload.submit_url === "string" ? payload.submit_url : ""', text)

    def test_submit_url_with_pin_is_refused(self):
        out = self.tmp / "locked.html"
        proc = run_render("render", str(EXAMPLE), "--out", str(out), "--pin",
                          "--submit-url", "http://127.0.0.1:8123/tok/submit")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("cannot be combined with --pin", proc.stderr)

    def test_requester_draft_keeps_requester_phase(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        data["requester_draft"] = {"picks": {"d1": {"c": "a"}}, "memos": {}, "note": "초안 메모"}
        path = self.write("with-draft.json", json.dumps(data, ensure_ascii=False))
        out = self.tmp / "draft.html"
        proc = run_render("render", str(path), "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("phase=requester", proc.stdout)
        payload, text = payload_of(out)
        self.assertEqual(payload["phase"], "requester")
        self.assertNotIn("requester_picks", payload["data"])
        self.assertEqual(payload["data"]["requester_draft"]["picks"]["d1"], {"c": "a"})

    def test_lock_roundtrip(self):
        out = self.tmp / "locked.html"
        proc = run_render("render", str(EXAMPLE), "--out", str(out), "--pin", "--pin-value", "1234")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload, _ = payload_of(out)
        blob = {k: payload[k] for k in ("salt", "iv", "ct", "iterations")}

        script = self.write("decrypt.mjs", DECRYPT_JS)
        body = json.dumps(blob)
        good = subprocess.run(["node", str(script), "1234"], input=body,
                              capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stderr)
        original = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(good.stdout), original)

        bad = subprocess.run(["node", str(script), "0000"], input=body,
                             capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.decisions = self.tmp / "decisions.json"
        self.decisions.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")

    def apply(self, text, name="reply.txt"):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return subprocess.run([sys.executable, str(RENDER), "apply", str(self.decisions), str(path)],
                              capture_output=True, text=True)

    def read(self):
        return json.loads(self.decisions.read_text(encoding="utf-8"))

    def test_apply_requester_writes_picks_and_memos(self):
        proc = self.apply(REQUESTER_REPLY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("applied: requester picks=4", proc.stdout)
        data = self.read()
        self.assertEqual(data["requester_picks"],
                         {"d1": {"c": "b"}, "d2": {"c": "a"}, "d3": {"c": "a"}, "d3r": {"c": "b"}})
        self.assertEqual(data["requester_memos"], {"d1": "첫 화면에서 이탈이 많습니다"})
        self.assertEqual(data["requester_note"], "전체 메모입니다")

    def test_apply_requester_drops_requester_draft(self):
        data = self.read()
        data["requester_draft"] = {"picks": {"d1": {"c": "a"}}, "memos": {"d2": "초안 메모"}, "note": "초안"}
        self.decisions.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        proc = self.apply(REQUESTER_REPLY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = self.read()
        self.assertNotIn("requester_draft", data)
        self.assertEqual(data["requester_picks"]["d1"], {"c": "b"})

    def test_apply_requester_custom_value(self):
        proc = self.apply(CUSTOM_REPLY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = self.read()
        self.assertEqual(data["requester_picks"]["d2"], {"c": "x", "x": "이메일 주소로 다시 받기"})
        self.assertEqual(data["requester_memos"], {})
        self.assertEqual(data["requester_note"], "")

    def test_apply_decider_writes_result_and_diff(self):
        self.assertEqual(self.apply(REQUESTER_REPLY).returncode, 0)
        proc = self.apply(DECIDER_REPLY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads((self.tmp / "decision-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["picks"],
                         {"d1": {"c": "a"}, "d2": {"c": "a"}, "d3": {"c": "a"}, "d3r": {"c": "b"}})
        self.assertEqual(result["memos"], {"d3": "완료 문구는 이대로 갑니다"})
        self.assertEqual(result["note"], "")
        self.assertEqual(result["diff_from_requester"], ["d1"])
        self.assertIn("1. 소셜 로그인을 첫 화면에 두는가: 이메일 가입만 둔다", proc.stdout)
        self.assertIn("요청자와 다른 선택: 1개", proc.stdout)
        self.assertFalse((self.tmp / "po-result.json").exists())

    def test_apply_legacy_po_reply_is_accepted(self):
        self.assertEqual(self.apply(REQUESTER_REPLY).returncode, 0)
        proc = self.apply(PO_REPLY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads((self.tmp / "decision-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["picks"],
                         {"d1": {"c": "a"}, "d2": {"c": "a"}, "d3": {"c": "a"}, "d3r": {"c": "b"}})
        self.assertEqual(result["diff_from_requester"], ["d1"])

    def test_apply_decider_on_skipped_requester_has_no_diff(self):
        data = self.read()
        data["skip_requester"] = True
        self.decisions.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        proc = self.apply(DECIDER_REPLY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads((self.tmp / "decision-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["picks"],
                         {"d1": {"c": "a"}, "d2": {"c": "a"}, "d3": {"c": "a"}, "d3r": {"c": "b"}})
        self.assertNotIn("diff_from_requester", result)
        self.assertNotIn("요청자와 다른 선택", proc.stdout)

    def test_english_copy_text_applies(self):
        data = json.loads(EN_EXAMPLE.read_text(encoding="utf-8"))
        self.decisions.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        proc = self.apply(EN_REPLY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("applied: requester picks=4", proc.stdout)
        data = self.read()
        self.assertEqual(data["requester_picks"],
                         {"d1": {"c": "b"}, "d2": {"c": "x", "x": "Ask for the email again"},
                          "d3": {"c": "a"}, "d3r": {"c": "b"}})
        self.assertEqual(data["requester_memos"], {"d1": "keep it above the fold"})
        self.assertEqual(data["requester_note"], "Ship it this week")

    def test_apply_requester_reply_on_skipped_data_is_refused(self):
        data = self.read()
        data["skip_requester"] = True
        self.decisions.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        proc = self.apply(REQUESTER_REPLY)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("requester phase was skipped", proc.stderr)

    def test_version_mismatch(self):
        proc = self.apply(REQUESTER_REPLY.replace("|v1|", "|v2|"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("version mismatch", proc.stderr)

    def test_unknown_part_id(self):
        proc = self.apply(REQUESTER_REPLY.replace("d1=b", "d9=b"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown part id: d9", proc.stderr)

    def test_unknown_option(self):
        proc = self.apply(REQUESTER_REPLY.replace("d1=b", "d1=z"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown option z for part d1", proc.stderr)

    def test_missing_code_line(self):
        proc = self.apply("아무 내용도 없습니다\n")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no selection code line", proc.stderr)


class I18nTests(unittest.TestCase):
    def dictionary(self):
        text = (HERE / "template.html").read_text(encoding="utf-8")
        match = I18N_RE.search(text)
        self.assertIsNotNone(match, "I18N 사전을 찾지 못했다")
        return json.loads(match.group(0)[len("var I18N = "):-1])

    def test_both_languages_carry_the_same_keys(self):
        table = self.dictionary()
        self.assertEqual(set(table["ko"]), set(table["en"]))

    def test_english_dictionary_has_no_korean(self):
        table = self.dictionary()
        for key, value in table["en"].items():
            self.assertFalse(any("가" <= char <= "힣" for char in value),
                             "en 문구에 한국어가 남아 있다: %s" % key)


if __name__ == "__main__":
    unittest.main()
