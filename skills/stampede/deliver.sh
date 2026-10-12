#!/usr/bin/env bash
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
RENDER="$HERE/render.py"
ORCA_BIN="${ORCA_BIN:-orca}"
PYTHON="${PYTHON:-python3}"
PUBLISHERS_DIR="${STAMPEDE_PUBLISHERS_DIR:-$HOME/.config/stampede/publishers}"

usage() {
  printf 'usage: %s --name <name> --dir <state-dir> [--start publisher:<file>|orca|claude]\n' "${0##*/}" >&2
  exit 2
}

log_skip() { printf 'skip: %s %s\n' "$1" "$2" >&2; }
first_line() {
  local line
  line=$(printf '%s' "$1" | head -n 1)
  [[ -n "$line" ]] || line=${2:-}
  printf '%s' "$line"
}
last_line() {
  printf '%s' "$1" | grep -v '^[[:space:]]*$' | tail -n 1
}

bin_available() {
  if [[ "$1" == */* ]]; then [[ -x "$1" ]]; else command -v "$1" >/dev/null 2>&1; fi
}

state_get() {
  [[ -f "$state" ]] || return 0
  "$PYTHON" - "$state" "$1" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    data = {}
value = data.get(sys.argv[2]) if isinstance(data, dict) else None
if value not in (None, ""):
    print(value)
PY
}

state_set() {
  "$PYTHON" - "$state" "$1" "$2" "$3" <<'PY'
import json, sys
path, channel, url, pin = sys.argv[1:5]
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception:
    data = {}
if not isinstance(data, dict):
    data = {}
if channel:
    data["channel"] = channel
if url:
    data["url"] = url
if pin:
    data["pin"] = pin
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
}

extract_url() {
  # The python program itself arrives on stdin, so the raw output travels via APQ_RAW.
  APQ_RAW="$1" "$PYTHON" - <<'PY'
import json, os, re
raw = os.environ.get("APQ_RAW", "")
KEYS = ("shareUrl", "url", "webUrl", "share_url", "link")


def dig(obj):
    if isinstance(obj, dict):
        for key in KEYS:
            value = obj.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for value in obj.values():
            found = dig(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = dig(value)
            if found:
                return found
    return None


url = None
try:
    url = dig(json.loads(raw))
except Exception:
    url = None
if not url:
    for line in raw.splitlines():
        try:
            url = dig(json.loads(line.strip()))
        except Exception:
            url = None
        if url:
            break
if not url:
    match = re.search(r'https?://[^\s"\'<>]+', raw)
    if match:
        url = match.group(0)
if url:
    print(url)
PY
}

name=""
dir=""
start=""
start_explicit=0

while (( $# > 0 )); do
  case "$1" in
    --name) (( $# >= 2 )) || usage; name=$2; shift 2 ;;
    --dir) (( $# >= 2 )) || usage; dir=$2; shift 2 ;;
    --start) (( $# >= 2 )) || usage; start=$2; start_explicit=1; shift 2 ;;
    *) usage ;;
  esac
done

[[ -n "$name" && -n "$dir" ]] || usage

decisions="$dir/decisions.json"
state="$dir/state.json"
[[ -f "$decisions" ]] || { printf 'error: %s not found\n' "$decisions" >&2; exit 2; }

if (( start_explicit == 0 )); then
  case "$(state_get channel)" in
    publisher:*) start=$(state_get channel) ;;
    orca) start=orca ;;
    claude-artifact) start=claude ;;
    *) start="" ;;
  esac
fi

case "$start" in
  ""|orca|claude) ;;
  publisher:*) ;;
  *) usage ;;
esac

want_publishers=1
[[ "$start" == orca || "$start" == claude ]] && want_publishers=0

# 발행기는 파일명 정렬 순서대로 시도한다. --start 또는 state.json 이 가리키는 발행기가
# 있으면 그 앞의 발행기는 건너뛴다.
publishers=()
if [[ -d "$PUBLISHERS_DIR" ]]; then
  for entry in "$PUBLISHERS_DIR"/*; do
    [[ -f "$entry" && -x "$entry" ]] && publishers+=("$entry")
  done
fi

start_index=0
if [[ "$start" == publisher:* ]]; then
  wanted=${start#publisher:}
  found=""
  for i in "${!publishers[@]}"; do
    if [[ "${publishers[$i]##*/}" == "$wanted" ]]; then found=$i; break; fi
  done
  [[ -n "$found" ]] && start_index=$found
fi

pin=$(state_get pin)
render_args=(--pin)
[[ -n "$pin" ]] && render_args+=(--pin-value "$pin")
lock_out=$("$PYTHON" "$RENDER" render "$decisions" --out "$dir/page-locked.html" "${render_args[@]}" 2>&1)
lock_rc=$?
if (( lock_rc != 0 )); then
  printf 'error: locked render failed: %s\n' "$(first_line "$lock_out")" >&2
  exit 2
fi
pin=$(printf '%s\n' "$lock_out" | sed -n 's/^pin: //p' | tail -n 1)

if (( want_publishers == 1 )) && (( ${#publishers[@]} > start_index )); then
  err_file=$(mktemp)
  page_out=$("$PYTHON" "$RENDER" render "$decisions" --out "$dir/page.html" 2>&1)
  page_rc=$?
  if (( page_rc != 0 )); then
    log_skip publisher "render failed: $(first_line "$page_out")"
  else
    for (( i = start_index; i < ${#publishers[@]}; i++ )); do
      publisher="${publishers[$i]}"
      label="publisher:${publisher##*/}"
      : > "$err_file"
      pub_out=$("$publisher" --name "$name" --dir "$dir" --page "$dir/page.html" \
                    --locked-page "$dir/page-locked.html" --pin "$pin" 2>"$err_file")
      pub_rc=$?
      pub_err=$(cat "$err_file")
      line=$(last_line "$pub_out")
      if (( pub_rc == 0 )) && [[ "$line" == delivered:* ]]; then
        rest=${line#delivered: }
        url=${rest%% *}
        pub_pin=""
        [[ "$rest" == *" pin="* ]] && pub_pin=${rest##* pin=}
        if [[ -n "$url" ]]; then
          state_set "$label" "$url" "$pub_pin"
          if [[ -n "$pub_pin" ]]; then
            printf 'delivered: %s %s pin=%s\n' "$label" "$url" "$pub_pin"
          else
            printf 'delivered: %s %s\n' "$label" "$url"
          fi
          rm -f "$err_file"
          exit 0
        fi
      fi
      reason=$(last_line "$pub_out")
      [[ "$reason" == skip:* ]] || reason=$(last_line "$pub_err")
      if [[ "$reason" == skip:* ]]; then
        reason=${reason#skip: }
      else
        reason=$(first_line "$pub_out" "")
        [[ -n "$reason" ]] || reason=$(first_line "$pub_err" "no output")
      fi
      log_skip "$label" "$reason"
    done
  fi
  rm -f "$err_file"
fi

if [[ "$start" != claude ]]; then
  if ! bin_available "$ORCA_BIN"; then
    log_skip orca "orca not found at $ORCA_BIN"
  else
    if [[ "$(state_get channel)" == orca ]]; then
      share_out=$("$ORCA_BIN" artifacts update "$dir/page-locked.html" --json 2>&1)
      share_rc=$?
    else
      share_out=$("$ORCA_BIN" artifacts share "$dir/page-locked.html" --json 2>&1)
      share_rc=$?
    fi
    url=$(extract_url "$share_out")
    if (( share_rc == 0 )) && [[ -n "$url" ]]; then
      state_set orca "$url" "$pin"
      printf 'delivered: orca-artifact %s pin=%s\n' "$url" "$pin"
      exit 0
    fi
    log_skip orca "$(first_line "$share_out" "no url in output")"
  fi
fi

state_set claude-artifact "$dir/page-locked.html" "$pin"
printf 'fallback: claude-artifact %s pin=%s\n' "$dir/page-locked.html" "$pin"
exit 3
