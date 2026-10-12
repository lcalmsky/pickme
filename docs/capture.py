#!/usr/bin/env python3
"""docs/demos 의 데모 데이터를 렌더해 README 예시 그림을 만든다. Chrome 헤드리스 실행 파일이 필요하다."""
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RENDER = os.path.join(REPO, 'skills', 'pickme', 'render.py')

CHROME = os.environ.get('CHROME') or '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
WIDTH = 1200
# 창 폭은 README 본문에 맞춘 1200 CSS 다. 배율 2 로 찍으므로 결과는 2400px 이 된다.
SCALE = '2'

# 결정권자 페이지의 선택 상태는 localStorage 나 컨트롤 이벤트로 복원되므로, 라디오에
# change 이벤트를 던져 상태를 만든다. 메모 칸은 input 이벤트를 받는다.
INJECT = """<script>(function(){
function fire(node, type) { node.dispatchEvent(new Event(type, { bubbles: true })); }
var picks = %s;
Object.keys(picks).forEach(function (id) {
  var pick = picks[id];
  var radio = document.getElementById("apq-" + id + "-" + pick.c);
  if (!radio) return;
  radio.checked = true;
  if (pick.c === "x") {
    var opt = radio.closest(".opt");
    var input = opt && opt.querySelector(".custom-input");
    if (input) { input.value = pick.x; fire(input, "input"); }
  }
  fire(radio, "change");
});
var memos = %s;
Object.keys(memos).forEach(function (id) {
  var card = document.getElementById("apq-dec-" + id);
  var area = card && card.querySelector(".memo textarea");
  if (!area) return;
  area.value = memos[id];
  fire(area, "input");
});
// 모두 고르면 뜨는 토스트는 캡처 시점에 따라 찍히기도 해서 그림마다 결과가 달라진다
document.querySelectorAll(".toast").forEach(function (node) { node.style.display = "none"; });
console.log("pickme-page-height:" + Math.ceil(document.documentElement.scrollHeight));
})();</script>"""

# 데모마다 결정권자가 고른 상태. 값은 docs/demos 의 데이터와 맞춰 둔다.
DEMO_STATE = {
    'requester': ({}, {}),
    'decider': (
        {'d1': {'c': 'b'}, 'd2': {'c': 'a'}, 'd3': {'c': 'b'}, 'd3r': {'c': 'b'}},
        {'d3': '완료 문구는 가입 알림 메일에도 그대로 넣습니다'},
    ),
    'designer': ({'d1': {'c': 'b'}, 'd2': {'c': 'x', 'x': '이메일 주소로 다시 받기'}}, {}),
}
DEMO_STATE_EN = {
    'requester': ({}, {}),
    'decider': (
        {'d1': {'c': 'b'}, 'd2': {'c': 'a'}, 'd3': {'c': 'b'}, 'd3r': {'c': 'b'}},
        {'d3': 'The finish wording goes into the signup email unchanged'},
    ),
    'designer': ({'d1': {'c': 'b'}, 'd2': {'c': 'x', 'x': 'Ask for the email again'}}, {}),
}

SIZE_MARK = 'pickme-page-height:'
MEASURE_H = 400


def die(msg):
    sys.stderr.write('capture.py: ' + msg + '\n')
    sys.exit(1)


def chrome_shot(args, out_path, timeout=120):
    # 이전 결과가 남아 있으면 그것을 새 결과로 잘못 볼 수 있어 지우고 시작한다.
    if os.path.exists(out_path):
        os.remove(out_path)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + timeout
    shot = False
    while time.time() < deadline:
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            # 파일이 다 쓰인 뒤에도 프로세스가 끝나지 않는 경우가 있어 크기가 멈추면 끝난 것으로 본다.
            size, settle = -1, time.time() + 5
            while time.time() < settle:
                time.sleep(0.3)
                current = os.path.getsize(out_path)
                if current == size:
                    break
                size = current
            shot = True
            break
        if proc.poll() is not None:
            shot = os.path.exists(out_path) and os.path.getsize(out_path) > 0
            break
        time.sleep(0.2)
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)
    if not shot:
        die('Chrome 이 %s 를 만들지 못했다' % out_path)


def chrome_args(ud_dir, window, extra):
    return [
        CHROME, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
        '--disable-crash-reporter', '--hide-scrollbars', '--force-device-scale-factor=' + SCALE,
        '--virtual-time-budget=5000', '--user-data-dir=' + ud_dir, '--window-size=%d,%d' % window,
    ] + extra


def page_height(html, ud_dir):
    """페이지가 알려 준 문서 높이(CSS px)."""
    # Chrome 은 그림을 다 쓴 뒤에도 끝나지 않을 때가 있어, 값을 읽으면 바로 끝낸다.
    args = chrome_args(ud_dir, (WIDTH, MEASURE_H),
                       ['--enable-logging=stderr', '--v=1', 'file://' + html])
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    fd = proc.stderr.fileno()
    buf = b''
    deadline = time.time() + 60
    try:
        while time.time() < deadline:
            if select.select([fd], [], [], 0.5)[0]:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                buf += chunk
                found = re.search(re.escape(SIZE_MARK.encode()) + rb'(\d+)', buf)
                if found:
                    return int(found.group(1))
            elif proc.poll() is not None:
                break
        return None
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        shutil.rmtree(ud_dir, ignore_errors=True)


def main(argv):
    lang, theme, rest = 'ko', 'light', sys.argv[1:]
    if rest and not rest[0].startswith('--'):
        demo, rest = rest[0], rest[1:]
    else:
        demo = None
    while rest:
        flag, rest = rest[0], rest[1:]
        if flag == '--lang' and rest:
            lang, rest = rest[0], rest[1:]
        elif flag == '--theme' and rest:
            theme, rest = rest[0], rest[1:]
        else:
            die('사용법: capture.py <requester|decider|designer|dark> [--lang ko|en] [--theme light|dark]')

    if demo == 'dark':
        theme, demo = 'dark', 'decider'
    if demo not in DEMO_STATE or lang not in ('ko', 'en') or theme not in ('light', 'dark'):
        die('사용법: capture.py <requester|decider|designer|dark> [--lang ko|en] [--theme light|dark]')
    if not os.path.exists(CHROME):
        die('Chrome 을 찾지 못했다: %s (환경 변수 CHROME 로 지정)' % CHROME)

    demos_dir = os.path.join(HERE, 'demos', 'en') if lang == 'en' else os.path.join(HERE, 'demos')
    src = os.path.join(demos_dir, demo + '.json')
    name = demo if theme == 'light' else 'dark'
    out_png = os.path.join(HERE, '%s.en.png' % name if lang == 'en' else '%s.png' % name)

    work_dir = tempfile.mkdtemp(prefix='pickme-capture-')
    ud_root = tempfile.mkdtemp(prefix='pickme-chrome-')
    try:
        html = os.path.join(work_dir, '%s.%s.%s.html' % (demo, lang, theme))
        cmd = [sys.executable, RENDER, 'render', src, '--out', html]
        if demo == 'requester':
            # 요청자 단계는 serve.py 가 연 페이지처럼 보여야 해서 전송 주소를 넣는다. 실제로 보내지는 않는다.
            cmd += ['--submit-url', 'http://127.0.0.1:8765/demo/submit']
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            die('%s 렌더 실패: %s' % (demo, (result.stderr or result.stdout).strip()))

        picks, memos = (DEMO_STATE_EN if lang == 'en' else DEMO_STATE)[demo]
        with open(html, encoding='utf-8') as handle:
            page = handle.read()
        fix = ''
        if theme == 'dark':
            fix += '<script>document.documentElement.setAttribute("data-theme", "dark");</script>'
        fix += INJECT % (json.dumps(picks, ensure_ascii=False), json.dumps(memos, ensure_ascii=False))
        page = page.replace('</body>', fix + '</body>', 1)
        with open(html, 'w', encoding='utf-8') as handle:
            handle.write(page)

        height = page_height(html, os.path.join(ud_root, 'ud-measure'))
        if height is None:
            die('페이지가 문서 높이를 알려 주지 않았다: %s' % html)
        # 캡처가 실패해도 기존 그림을 잃지 않게 임시 파일에 찍고 옮긴다.
        tmp_png = out_png + '.tmp.png'
        chrome_shot(chrome_args(os.path.join(ud_root, 'ud-shot'), (WIDTH, height),
                                ['--screenshot=' + tmp_png, 'file://' + html]), tmp_png)
        os.replace(tmp_png, out_png)
        print('%s: %s (%dx%d)' % (name, out_png, WIDTH, height))
    finally:
        shutil.rmtree(ud_root, ignore_errors=True)
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == '__main__':
    main(sys.argv)
