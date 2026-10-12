#!/usr/bin/env python3
"""pickme 설정 파일을 읽고 쓴다. 지금 키는 요청자 페이지를 여는 곳(`open`) 하나뿐이다."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

KEYS = {"open": ("auto", "orca", "browser", "none")}


class ConfigError(Exception):
    pass


def config_path():
    override = os.environ.get("PICKME_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "pickme" / "config.json"


def load():
    path = config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError("cannot read %s: %s" % (path, exc))
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ConfigError("invalid JSON in %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ConfigError("%s is not a JSON object" % path)
    return data


def get(key):
    return load().get(key)


def save(data):
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".json")
    except OSError as exc:
        raise ConfigError("cannot write %s: %s" % (path, exc))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, str(path))
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise ConfigError("cannot write %s: %s" % (path, exc))


def fail(message):
    print("error: " + message, file=sys.stderr)
    sys.exit(2)


def cmd_get(args):
    if args.key not in KEYS:
        fail("unknown key: %s" % args.key)
    try:
        value = get(args.key)
    except ConfigError as exc:
        fail(str(exc))
    print(value if value else "unset")


def cmd_set(args):
    # 값을 먼저 검사해 오타 하나가 저장된 설정을 지우지 않게 한다
    if args.key not in KEYS:
        fail("unknown key: %s" % args.key)
    allowed = KEYS[args.key]
    if args.value not in allowed:
        fail("invalid value for %s: %s (allowed: %s)" % (args.key, args.value, ", ".join(allowed)))
    # 깨진 파일을 새 값으로 덮어쓰지 않는다. 무엇이 깨졌는지 먼저 알린다
    try:
        data = load()
    except ConfigError as exc:
        fail(str(exc))
    data[args.key] = args.value
    try:
        save(data)
    except ConfigError as exc:
        fail(str(exc))
    print("saved: %s=%s" % (args.key, args.value))


def cmd_path(args):
    print(config_path())


def main(argv):
    parser = argparse.ArgumentParser(prog="config.py")
    sub = parser.add_subparsers(dest="command", required=True)

    get_parser = sub.add_parser("get")
    get_parser.add_argument("key")
    get_parser.set_defaults(func=cmd_get)

    set_parser = sub.add_parser("set")
    set_parser.add_argument("key")
    set_parser.add_argument("value")
    set_parser.set_defaults(func=cmd_set)

    path_parser = sub.add_parser("path")
    path_parser.set_defaults(func=cmd_path)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
