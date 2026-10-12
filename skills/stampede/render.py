#!/usr/bin/env python3
import argparse
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
LOCK_JS = HERE / "lock.mjs"
PBKDF2_ITERATIONS = 200000
CODE_PREFIX = "apq"
HTML_OPEN = '<html lang="ko">'

# 복사 텍스트는 두 언어 모두 파싱된다. 머리말은 I18N 의 structure 키와 맞춰 둔다.
CODE_LINE_RE = re.compile(r"^(?:선택 코드|Selection code):\s*(.*)$")
PICK_RE = re.compile(r"^([A-Za-z0-9_]+)=([a-z])(?:\((.*)\))?$")
ITEM_RE = re.compile(r"^\s*(\d+)\.\s")
MEMO_RE = re.compile(r"^\s{2,}(?:메모|Memo):\s?(.*)$")
NOTE_RE = re.compile(r"^(?:전체 메모|Overall memo):\s?(.*)$")
VERSION_RE = re.compile(r"^v(\d+)$")

LANGS = ("ko", "en")
ROLE_DEFAULT = {"ko": "결정권자", "en": "Decider"}
TITLE = {"ko": "%s 결정 요청", "en": "Decision request for %s"}
CUSTOM_LABEL = {"ko": "직접 입력", "en": "Custom input"}
DIFF_LINE = {"ko": "요청자와 다른 선택: %d개", "en": "Different from the requester: %d"}


def fail(message):
    print("error: " + message, file=sys.stderr)
    sys.exit(2)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError:
        fail("cannot read %s" % path)
    except ValueError:
        fail("invalid JSON in %s" % path)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def lang_of(data):
    meta = data.get("meta") or {}
    lang = (meta.get("lang") or "").strip().lower()
    return lang if lang in LANGS else LANGS[0]


def decider_role(data):
    meta = data.get("meta") or {}
    role = (meta.get("decider_role") or "").strip()
    if role:
        return role
    # Pages written before the decider role existed carry only meta.po, which was always a PO.
    return "PO" if meta.get("po") else ROLE_DEFAULT[lang_of(data)]


def decider_name(data):
    meta = data.get("meta") or {}
    return meta.get("decider") or meta.get("po") or ""


def phase_of(data):
    return "decider" if (data.get("requester_picks") or data.get("skip_requester")) else "requester"


def build_page(title, payload, lang):
    template = TEMPLATE.read_text(encoding="utf-8")
    # A JSON string containing "</script" would end the embedded script block early.
    blob = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    html_open = '<html lang="%s">' % lang
    if template.count(HTML_OPEN) != 1:
        fail("template does not carry exactly one %s" % HTML_OPEN)
    page = template.replace(HTML_OPEN, html_open).replace("__APQ_TITLE__", html.escape(title))
    return page.replace("__APQ_PAYLOAD__", blob)


def lock_payload(data, pin):
    node = os.environ.get("APQ_NODE") or shutil.which("node") or "node"
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    try:
        proc = subprocess.run([node, str(LOCK_JS), pin], input=body,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        fail("cannot run node: %s" % exc)
    if proc.returncode != 0:
        fail("lock failed: %s" % proc.stderr.decode("utf-8", "replace").strip()[:300])
    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except ValueError:
        fail("lock produced invalid JSON")


def cmd_render(args):
    data = read_json(args.data)
    phase = phase_of(data)
    lang = lang_of(data)
    role = decider_role(data)
    page_title = TITLE[lang] % role
    locked = args.pin or args.pin_value is not None
    # submit_url 은 요청자 단계를 로컬로 여는 잠금 없는 페이지 전용이다
    if args.submit_url and locked:
        fail("--submit-url cannot be combined with --pin")
    pin_value = None
    if locked:
        pin_value = args.pin_value or "%04d" % secrets.randbelow(10000)
        if not re.fullmatch(r"[0-9]{4}", pin_value):
            fail("pin must be 4 digits")
        # The lock screen renders before decryption, so the role reaches it through its own key.
        payload = {"locked": True, "title": page_title}
        payload.update(lock_payload(data, pin_value))
        payload["iterations"] = PBKDF2_ITERATIONS
        title = page_title
    else:
        payload = {"locked": False, "phase": phase, "data": data}
        if args.submit_url:
            payload["submit_url"] = args.submit_url
        title = (data.get("meta") or {}).get("title") or page_title
    payload["lang"] = lang

    Path(args.out).write_text(build_page(title, payload, lang), encoding="utf-8")
    print("rendered: %s phase=%s" % (args.out, phase))
    if locked:
        print("pin: %s" % pin_value)


def parts_index(data):
    index = {}
    for decision in data.get("decisions") or []:
        for part in decision.get("parts") or []:
            index[part["id"]] = part
    return index


def parse_code(code):
    fields = code.split("|")
    if len(fields) < 4 or fields[0] != CODE_PREFIX:
        fail("bad selection code: %s" % code)
    version_match = VERSION_RE.match(fields[2])
    if not version_match:
        fail("bad version in selection code: %s" % fields[2])
    phase = fields[3]
    # Pages published before the phase was renamed still reply with "po".
    if phase == "po":
        phase = "decider"
    if phase not in ("requester", "decider"):
        fail("bad phase in selection code: %s" % phase)
    picks = {}
    for chunk in (fields[4] if len(fields) > 4 else "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = PICK_RE.match(chunk)
        if not match:
            fail("bad pick in selection code: %s" % chunk)
        pick = {"c": match.group(2)}
        if match.group(3) is not None:
            pick["x"] = match.group(3)
        picks[match.group(1)] = pick
    return {"name": fields[1], "version": int(version_match.group(1)), "phase": phase, "picks": picks}


def validate_picks(picks, index):
    for part_id, pick in picks.items():
        part = index.get(part_id)
        if part is None:
            fail("unknown part id: %s" % part_id)
        if pick["c"] == "x":
            if not part.get("allow_custom"):
                fail("part %s does not allow a custom value" % part_id)
        elif pick["c"] not in [o.get("id") for o in part.get("options") or []]:
            fail("unknown option %s for part %s" % (pick["c"], part_id))
    return picks


def parse_memos(text, data):
    decisions = data.get("decisions") or []
    memos = {}
    current = None
    for line in text.splitlines():
        item = ITEM_RE.match(line)
        if item:
            position = int(item.group(1)) - 1
            current = decisions[position] if 0 <= position < len(decisions) else None
            continue
        memo = MEMO_RE.match(line)
        if memo and current is not None:
            memos[current["id"]] = memo.group(1).strip()
    return memos


def parse_note(text):
    for line in text.splitlines():
        note = NOTE_RE.match(line)
        if note:
            return note.group(1).strip()
    return ""


def same_pick(a, b):
    if not a or not b:
        return False
    if a.get("c") != b.get("c"):
        return False
    if a.get("c") == "x":
        return (a.get("x") or "").strip() == (b.get("x") or "").strip()
    return True


def option_label(part, pick, lang=LANGS[0]):
    if pick.get("c") == "x":
        return (pick.get("x") or "").strip() or CUSTOM_LABEL[lang]
    for option in part.get("options") or []:
        if option.get("id") == pick["c"]:
            return option.get("label") or pick["c"]
    return pick["c"]


def cmd_apply(args):
    data = read_json(args.data)
    text = Path(args.pasted).read_text(encoding="utf-8")

    code = None
    for line in text.splitlines():
        match = CODE_LINE_RE.match(line.strip())
        if match:
            code = match.group(1).strip()
            break
    if not code:
        fail("no selection code line found")

    parsed = parse_code(code)
    lang = lang_of(data)
    meta = data.get("meta") or {}
    if parsed["name"] != (meta.get("name") or ""):
        fail("name mismatch (page %s, reply %s)" % (meta.get("name"), parsed["name"]))
    if parsed["version"] != int(meta.get("version") or 0):
        fail("version mismatch (page v%s, reply v%s)" % (meta.get("version"), parsed["version"]))

    picks = validate_picks(parsed["picks"], parts_index(data))
    memos = parse_memos(text, data)
    note = parse_note(text)

    if parsed["phase"] == "requester":
        if data.get("skip_requester"):
            fail("requester phase was skipped")
        data["requester_picks"] = picks
        data["requester_memos"] = memos
        data["requester_note"] = note
        data.pop("requester_draft", None)
        write_json(args.data, data)
        print("applied: requester picks=%d" % len(picks))
        return

    requester = data.get("requester_picks") or {}
    result = {"picks": picks, "memos": memos, "note": note}
    diff = []
    if requester:
        diff = [part_id for part_id, pick in picks.items() if not same_pick(requester.get(part_id), pick)]
        result["diff_from_requester"] = diff
    out = Path(args.data).resolve().with_name("decision-result.json")
    write_json(out, result)

    for position, decision in enumerate(data.get("decisions") or [], 1):
        dp = decision.get("parts") or []
        segments = []
        for part in dp:
            pick = picks.get(part["id"])
            if not pick:
                continue
            value = option_label(part, pick, lang)
            segments.append("%s: %s" % (part.get("label") or part["id"], value) if len(dp) > 1 else value)
        if segments:
            print("%d. %s: %s" % (position, decision.get("title"), " / ".join(segments)))
    if requester:
        print(DIFF_LINE[lang] % len(diff))


def main(argv):
    parser = argparse.ArgumentParser(prog="render.py")
    sub = parser.add_subparsers(dest="command", required=True)

    render_parser = sub.add_parser("render")
    render_parser.add_argument("data")
    render_parser.add_argument("--out", required=True)
    render_parser.add_argument("--pin", action="store_true")
    render_parser.add_argument("--pin-value", default=None)
    render_parser.add_argument("--submit-url", default=None)
    render_parser.set_defaults(func=cmd_render)

    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("data")
    apply_parser.add_argument("pasted")
    apply_parser.set_defaults(func=cmd_apply)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
