#!/usr/bin/env bash
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
DELIVER="$here/deliver.sh"
EXAMPLE="$here/example.json"

tmp=$(mktemp -d)
tmp=$(cd "$tmp" && pwd)
trap 'rm -rf "$tmp"' EXIT

export CALL_LOG="$tmp/calls.log"
export ORCA_BIN="$tmp/orca.sh"
export PICKME_PUBLISHERS_DIR="$tmp/publishers"
export PYTHON="${PYTHON:-python3}"

mkdir -p "$PICKME_PUBLISHERS_DIR"

cat > "$PICKME_PUBLISHERS_DIR/10-first.sh" <<'SH'
#!/usr/bin/env bash
printf 'pub first %s\n' "$*" >> "$CALL_LOG"
[[ -n "${FIRST_OUT:-}" ]] && printf '%s\n' "$FIRST_OUT"
[[ -n "${FIRST_ERR:-}" ]] && printf '%s\n' "$FIRST_ERR" >&2
exit "${FIRST_RC:-0}"
SH

cat > "$PICKME_PUBLISHERS_DIR/20-second.sh" <<'SH'
#!/usr/bin/env bash
printf 'pub second %s\n' "$*" >> "$CALL_LOG"
[[ -n "${SECOND_OUT:-}" ]] && printf '%s\n' "$SECOND_OUT"
exit "${SECOND_RC:-0}"
SH

cat > "$ORCA_BIN" <<'SH'
#!/usr/bin/env bash
printf 'orca %s\n' "$*" >> "$CALL_LOG"
case "${1:-} ${2:-}" in
  "artifacts share"|"artifacts update")
    [[ -n "${ORCA_SHARE_OUT:-}" ]] && printf '%s\n' "$ORCA_SHARE_OUT"
    [[ -n "${ORCA_SHARE_ERR:-}" ]] && printf '%s\n' "$ORCA_SHARE_ERR" >&2
    exit "${ORCA_SHARE_RC:-0}"
    ;;
esac
exit 0
SH

chmod +x "$PICKME_PUBLISHERS_DIR"/*.sh "$ORCA_BIN"

NAME=demo-signup-revamp
OUT="$tmp/out"; ERR="$tmp/err"; RC=0
pass=0; fail=0; CASE_STATUS=0; CASE_DESC=""

reset_stub_env() {
  unset FIRST_OUT FIRST_ERR FIRST_RC SECOND_OUT SECOND_RC \
        ORCA_SHARE_OUT ORCA_SHARE_ERR ORCA_SHARE_RC
  export PICKME_PUBLISHERS_DIR="$tmp/publishers"
  export ORCA_BIN="$tmp/orca.sh"
  export FIRST_OUT='delivered: https://pub.example/first?v=1'
  export FIRST_RC=0
  export SECOND_OUT='delivered: https://pub.example/second?v=1'
  export SECOND_RC=0
  export ORCA_SHARE_OUT='{"ok":true,"result":{"shareUrl":"https://share.onorca.dev/a/abc"}}'
  export ORCA_SHARE_RC=0
}

STATE_DIR=""

reset_state() {
  STATE_DIR="$tmp/state"
  rm -rf "$STATE_DIR"
  mkdir -p "$STATE_DIR"
  cp "$EXAMPLE" "$STATE_DIR/decisions.json"
}

run() {
  : > "$CALL_LOG"
  ( exec "$DELIVER" --name "$NAME" --dir "$STATE_DIR" "$@" ) >"$OUT" 2>"$ERR"
  RC=$?
}

start_case() { CASE_STATUS=0; CASE_DESC=$1; reset_stub_env; reset_state; }
end_case() {
  if (( CASE_STATUS == 0 )); then
    printf 'PASS %s\n' "$CASE_DESC"
    pass=$(( pass + 1 ))
  else
    printf 'FAIL %s\n' "$CASE_DESC"
    fail=$(( fail + 1 ))
  fi
}

expect_exit() {
  if [[ "$RC" == "$1" ]]; then return 0; fi
  printf '    exit: got %s want %s\n' "$RC" "$1"
  CASE_STATUS=1
}

expect_stdout() {
  if grep -qxF -- "$1" "$OUT"; then return 0; fi
  printf '    stdout missing line: %s\n' "$1"
  CASE_STATUS=1
}

expect_stdout_prefix() {
  if grep -q -- "^$1" "$OUT"; then return 0; fi
  printf '    stdout missing prefix: %s\n' "$1"
  CASE_STATUS=1
}

expect_stderr_contains() {
  if grep -qF -- "$1" "$ERR"; then return 0; fi
  printf '    stderr missing text: %s\n' "$1"
  CASE_STATUS=1
}

expect_stderr_empty() {
  if [[ -s "$ERR" ]]; then
    printf '    stderr not empty: %s\n' "$(head -n 1 "$ERR")"
    CASE_STATUS=1
  fi
}

expect_state() {
  if grep -qF -- "$1" "$STATE_DIR/state.json"; then return 0; fi
  printf '    state.json missing: %s\n' "$1"
  CASE_STATUS=1
}

expect_call() {
  if grep -q -- "$1" "$CALL_LOG"; then return 0; fi
  printf '    expected call matching: %s\n' "$1"
  CASE_STATUS=1
}

expect_no_call() {
  if grep -q -- "$1" "$CALL_LOG"; then
    printf '    unexpected call matching: %s\n' "$1"
    CASE_STATUS=1
  fi
}

expect_file() {
  if [[ -f "$1" ]]; then return 0; fi
  printf '    missing file: %s\n' "$1"
  CASE_STATUS=1
}

orca_url='https://share.onorca.dev/a/abc'

start_case "1: a publisher delivers the page"
run
expect_exit 0
expect_stdout 'delivered: publisher:10-first.sh https://pub.example/first?v=1'
expect_no_call '^orca '
expect_state '"channel": "publisher:10-first.sh"'
expect_file "$STATE_DIR/page.html"
expect_file "$STATE_DIR/page-locked.html"
# 발행기는 잠금 없는 페이지와 잠금 페이지를 함께 받고 어떤 쪽을 올릴지 스스로 정한다
expect_call "pub first --name $NAME --dir $STATE_DIR --page $STATE_DIR/page.html --locked-page $STATE_DIR/page-locked.html --pin [0-9][0-9][0-9][0-9]"
end_case

start_case "2: a publisher PIN is carried into the result"
export FIRST_OUT='delivered: https://pub.example/first pin=7788'
run
expect_exit 0
expect_stdout 'delivered: publisher:10-first.sh https://pub.example/first pin=7788'
expect_state '"pin": "7788"'
end_case

start_case "3: no publisher directory skips the stage without a skip line"
rm -rf "$STATE_DIR"; mkdir -p "$STATE_DIR"; cp "$EXAMPLE" "$STATE_DIR/decisions.json"
export PICKME_PUBLISHERS_DIR="$tmp/no-such-publishers"
run
expect_exit 0
expect_stdout_prefix "delivered: orca-artifact $orca_url pin="
expect_stderr_empty
expect_call '^orca artifacts share '
end_case

start_case "4: an empty publisher directory is skipped the same way"
export PICKME_PUBLISHERS_DIR="$tmp/empty-publishers"
mkdir -p "$PICKME_PUBLISHERS_DIR"
run
expect_exit 0
expect_stdout_prefix "delivered: orca-artifact $orca_url pin="
expect_stderr_empty
expect_call '^orca artifacts share '
end_case

start_case "5: the first publisher fails and the second delivers"
export FIRST_RC=1
export FIRST_OUT='boom'
run
expect_exit 0
expect_stdout 'delivered: publisher:20-second.sh https://pub.example/second?v=1'
expect_stderr_contains 'skip: publisher:10-first.sh boom'
expect_call '^pub second '
expect_no_call '^orca '
end_case

start_case "6: a publisher skip with a run hint falls through to orca"
export FIRST_RC=1
export FIRST_OUT='skip: vpn is down (run: vpn connect)'
export SECOND_RC=1
export SECOND_OUT='skip: not configured'
run
expect_exit 0
expect_stdout_prefix "delivered: orca-artifact $orca_url pin="
expect_stderr_contains 'skip: publisher:10-first.sh vpn is down (run: vpn connect)'
expect_stderr_contains 'skip: publisher:20-second.sh not configured'
expect_call '^orca artifacts share '
end_case

start_case "7: a skip line on stderr is read too"
export FIRST_RC=1
export FIRST_OUT='pushing'
export FIRST_ERR='skip: needs a login (run: pub login)'
run
expect_exit 0
expect_stderr_contains 'skip: publisher:10-first.sh needs a login (run: pub login)'
end_case

start_case "8: a republish starts from the channel in state.json"
printf '{"channel":"publisher:20-second.sh","url":"https://pub.example/second?v=1"}\n' > "$STATE_DIR/state.json"
run
expect_exit 0
expect_stdout_prefix 'delivered: publisher:20-second.sh '
expect_no_call '^pub first '
expect_call '^pub second '
end_case

start_case "9: --start names the publisher to reuse"
run --start publisher:20-second.sh
expect_exit 0
expect_stdout_prefix 'delivered: publisher:20-second.sh '
expect_no_call '^pub first '
end_case

start_case "10: --start orca skips the publishers"
run --start orca
expect_exit 0
expect_stdout_prefix "delivered: orca-artifact $orca_url pin="
expect_no_call '^pub '
expect_call '^orca artifacts share '
end_case

start_case "11: --start claude goes straight to the fallback"
run --start claude
expect_exit 3
expect_stdout_prefix 'fallback: claude-artifact '
expect_no_call '^pub '
expect_no_call '^orca '
expect_file "$STATE_DIR/page-locked.html"
end_case

start_case "12: no publisher and no orca falls back to claude-artifact"
export ORCA_BIN="$tmp/does-not-exist"
export FIRST_RC=1
export FIRST_OUT='boom'
export SECOND_RC=1
export SECOND_OUT='boom'
run
expect_exit 3
expect_stdout_prefix 'fallback: claude-artifact '
expect_stderr_contains 'skip: orca orca not found'
expect_file "$STATE_DIR/page-locked.html"
end_case

start_case "13: an unknown --start value is refused"
run --start nowhere
expect_exit 2
expect_no_call '^pub '
expect_no_call '^orca '
end_case

start_case "14: state channel orca reuses artifacts update"
printf '{"channel":"orca","url":"https://share.onorca.dev/a/abc","pin":"1234"}\n' > "$STATE_DIR/state.json"
run
expect_exit 0
expect_stdout "delivered: orca-artifact $orca_url pin=1234"
expect_call '^orca artifacts update '
expect_no_call '^orca artifacts share '
expect_no_call '^pub '
end_case

start_case "15: state channel claude-artifact reuses the PIN"
printf '{"channel":"claude-artifact","pin":"0421"}\n' > "$STATE_DIR/state.json"
run --start claude
expect_exit 3
expect_stdout "fallback: claude-artifact $STATE_DIR/page-locked.html pin=0421"
expect_no_call '^pub '
expect_no_call '^orca '
end_case

start_case "16: orca failing falls back to claude-artifact"
export ORCA_SHARE_RC=1
export ORCA_SHARE_OUT='{"ok":false}'
run --start orca
expect_exit 3
expect_stderr_contains 'skip: orca'
expect_stdout_prefix 'fallback: claude-artifact '
end_case

start_case "17: a publisher delivers even with a spaced state dir"
STATE_DIR="$tmp/spaced dir"
rm -rf "$STATE_DIR"; mkdir -p "$STATE_DIR"
cp "$EXAMPLE" "$STATE_DIR/decisions.json"
run
expect_exit 0
expect_stdout_prefix 'delivered: publisher:10-first.sh '
end_case

start_case "18: missing decisions.json"
STATE_DIR="$tmp/empty"
rm -rf "$STATE_DIR"; mkdir -p "$STATE_DIR"
run
expect_exit 2
expect_stderr_contains 'decisions.json not found'
expect_no_call '^pub '
expect_no_call '^orca '
end_case

printf '\n%s passed, %s failed\n' "$pass" "$fail"
(( fail == 0 ))
