"""The tool's own settings and caches, kept per user on the computer (never on the card).

  Windows  %APPDATA%\\GammaOS Pico Tool\\          (cache: %LOCALAPPDATA%\\GammaOS Pico Tool\\Cache)
  macOS    ~/Library/Application Support/GammaOS Pico Tool/   (cache: ~/Library/Caches/...)
  Linux    $XDG_CONFIG_HOME/gammaos-pico-tool/    (cache: $XDG_CACHE_HOME/gammaos-pico-tool/)

GAMMAOS_PICO_TOOL_HOME puts both in one folder (tests, portable use).
"""

import json
import os
import sys
import threading

APP_DIR_NAME = "GammaOS Pico Tool"
APP_DIR_UNIX = "gammaos-pico-tool"

DEFAULTS = {
    "accent": "Blue",
    "geometry": "",
    "last_card": "",
    "scrape_source": "screenscraper",
    "scrape_region": "wor",
    "ss_user": "",
    "ss_password": "",
    "tgdb_key": "",
    "scrape_overwrite": False,
    "backup_root": "",
    "auto_refresh": True,
    "include_demo_games": True,
    "console_model": "",        # "kpa" | "block": which console's packages to download ("" = kpa)
}


def config_dir():
    override = os.environ.get("GAMMAOS_PICO_TOOL_HOME")
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        return os.path.join(base, APP_DIR_NAME)
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/" + APP_DIR_NAME)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_DIR_UNIX)


def cache_dir():
    override = os.environ.get("GAMMAOS_PICO_TOOL_HOME")
    if override:
        return os.path.join(override, "cache")
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, APP_DIR_NAME, "Cache")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/" + APP_DIR_NAME)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, APP_DIR_UNIX)


def _write_json(path, obj, private=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Settings(object):
    """settings.json: a dict with defaults, saved whole on every change (it holds an account's
    password when the user gives one, so it is readable by the user only)."""

    def __init__(self, path=None):
        self.path = path or os.path.join(config_dir(), "settings.json")
        self.lock = threading.Lock()
        self.data = dict(DEFAULTS)
        try:
            with open(self.path, encoding="utf-8") as f:
                got = json.load(f)
            if isinstance(got, dict):
                self.data.update(got)
        except (OSError, ValueError):
            pass

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        self.update({key: value})

    def update(self, values):
        with self.lock:
            self.data.update(values)
            snap = dict(self.data)
        try:
            _write_json(self.path, snap, private=True)
        except OSError:
            pass


class JsonCache(object):
    """A small persistent key/value store (archive contents, ROM CRCs), saved on demand."""

    def __init__(self, name, limit=200000):
        self.path = os.path.join(cache_dir(), name + ".json")
        self.limit = limit
        self.lock = threading.Lock()
        self.data = {}
        self.dirty = False
        try:
            with open(self.path, encoding="utf-8") as f:
                got = json.load(f)
            if isinstance(got, dict):
                self.data = got
        except (OSError, ValueError):
            pass

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def __setitem__(self, key, value):
        with self.lock:
            self.data[key] = value
            self.dirty = True

    def __getitem__(self, key):
        with self.lock:
            return self.data[key]

    def __contains__(self, key):
        with self.lock:
            return key in self.data

    def save(self):
        with self.lock:
            if not self.dirty:
                return
            if len(self.data) > self.limit:
                keys = list(self.data)[-self.limit:]
                self.data = {k: self.data[k] for k in keys}
            snap = dict(self.data)
            self.dirty = False
        try:
            _write_json(self.path, snap)
        except OSError:
            pass
