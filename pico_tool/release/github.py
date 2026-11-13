"""The latest GammaOS Pico release from GitHub, and its files downloaded and checked.

Uses only the standard library (urllib, so the system's proxy settings apply). Every release has
gammaos-pico-kpa-<version>-sdcard.zip, gammaos-pico-kpa-<version>-flash.zip and SHA256SUMS; a
download is kept in the tool's cache folder and checked against SHA256SUMS before it is used.
"""

import hashlib
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request

from .. import REPO, __version__
from ..config import cache_dir

API_LATEST = "https://api.github.com/repos/%s/releases/latest" % REPO
UA = "gammaos-pico-tool/" + __version__
CARD_RE = re.compile(r"^gammaos-pico-kpa-(.+)-sdcard\.zip$")
FLASH_RE = re.compile(r"^gammaos-pico-kpa-(.+)-flash\.zip$")


class ReleaseError(Exception):
    """str() is a sentence for the user."""


class Asset(object):
    def __init__(self, name, url, size):
        self.name, self.url, self.size = name, url, size


class Release(object):
    def __init__(self, tag, name, version, card, flash, sums, notes="", url="", published=""):
        self.tag, self.name, self.version = tag, name, version
        self.card, self.flash, self.sums = card, flash, sums
        self.notes, self.url, self.published = notes, url, published


def _open(url, timeout=30, accept=None):
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    try:
        req = urllib.request.Request(url, headers=headers)
    except ValueError:
        raise ReleaseError("The release lists a download address that cannot be used (%s)." % url)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ReleaseError("GammaOS Pico has no release on GitHub yet.")
        if e.code in (403, 429):
            reset = e.headers.get("X-RateLimit-Reset") if e.headers else None
            remaining = e.headers.get("X-RateLimit-Remaining") if e.headers else None
            if remaining == "0" and reset and reset.isdigit():
                mins = max(1, int((int(reset) - time.time()) / 60 + 0.5))
                raise ReleaseError("GitHub's limit for anonymous requests was reached. Try again in about "
                                   "%d minute%s, or pick a downloaded package with \"Use a zip file...\"."
                                   % (mins, "" if mins == 1 else "s"))
            raise ReleaseError("GitHub refused the request (HTTP %d). Try again later, or pick a downloaded "
                               "package with \"Use a zip file...\"." % e.code)
        raise ReleaseError("GitHub answered with an error (HTTP %d). Try again later." % e.code)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise ReleaseError("GitHub did not answer in time. Check the connection and try again.")
        raise ReleaseError("GitHub could not be reached (%s). The tool works offline with a package you "
                           "downloaded before: \"Use a zip file...\"." % reason)
    except (socket.timeout, TimeoutError):
        raise ReleaseError("GitHub did not answer in time. Check the connection and try again.")


def latest(timeout=20):
    """The latest release, or raises ReleaseError."""
    with _open(API_LATEST, timeout, "application/vnd.github+json") as r:
        try:
            info = json.loads(r.read().decode("utf-8"))
        except ValueError:
            raise ReleaseError("GitHub's answer could not be read.")
    return parse_release(info)


def parse_release(info):
    card = flash = sums = None
    version = None
    for a in info.get("assets") or ():
        name = a.get("name") or ""
        asset = Asset(name, a.get("browser_download_url") or "", int(a.get("size") or 0))
        m = CARD_RE.match(name)
        if m:
            card = asset
            version = m.group(1)
        elif FLASH_RE.match(name):
            flash = asset
            version = version or FLASH_RE.match(name).group(1)
        elif name == "SHA256SUMS":
            sums = asset
    tag = info.get("tag_name") or ""
    if not card:
        raise ReleaseError("The latest release (%s) has no microSD card package." % (tag or "untitled"))
    return Release(tag, info.get("name") or tag, version or tag.lstrip("v"), card, flash, sums,
                   info.get("body") or "", info.get("html_url") or "", info.get("published_at") or "")


def parse_sums(text):
    """{file name: sha256} from a SHA256SUMS file ("<hash>  <name>" or "<hash> *<name>")."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out[os.path.basename(m.group(2))] = m.group(1).lower()
    return out


def sha256_file(path, cancel=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise ReleaseError("Cancelled.")
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def fetch_sums(release, timeout=30):
    if not release.sums:
        raise ReleaseError("The release has no SHA256SUMS file to check its packages against.")
    with _open(release.sums.url, timeout) as r:
        return parse_sums(r.read().decode("utf-8", "replace"))


def download(asset, sums, progress=None, cancel=None, dest_dir=None):
    """The asset in the cache folder, checked against its sha256 (downloaded when it is not there
    already). progress(done, total). Returns the path."""
    want = sums.get(asset.name)
    if not want:
        raise ReleaseError("SHA256SUMS does not list %s, so it cannot be checked." % asset.name)
    d = dest_dir or os.path.join(cache_dir(), "downloads")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, asset.name)
    if os.path.isfile(path):
        try:
            if sha256_file(path, cancel) == want:
                return path
        except OSError:
            pass
    part = path + ".part"
    h = hashlib.sha256()
    done = 0
    with _open(asset.url, 60) as r:
        total = int(r.headers.get("Content-Length") or asset.size or 0)
        with open(part, "wb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    f.close()
                    _rm(part)
                    raise ReleaseError("The download was cancelled.")
                try:
                    b = r.read(256 << 10)
                except (socket.timeout, TimeoutError, OSError) as e:
                    f.close()
                    _rm(part)
                    raise ReleaseError("The download stopped (%s). Try again." % e)
                if not b:
                    break
                f.write(b)
                h.update(b)
                done += len(b)
                if progress:
                    progress(done, total)
    if h.hexdigest() != want:
        _rm(part)
        raise ReleaseError("%s does not match its SHA256SUMS entry: the download is damaged. Try again."
                           % asset.name)
    os.replace(part, path)
    return path


def verify_local(path):
    """Check a package the user picked against a SHA256SUMS beside it, when there is one.
    Returns (checked, message); raises ReleaseError on a mismatch."""
    sums_path = os.path.join(os.path.dirname(os.path.abspath(path)), "SHA256SUMS")
    if not os.path.isfile(sums_path):
        return False, "No SHA256SUMS beside the file: it was not checked."
    with open(sums_path, encoding="utf-8", errors="replace") as f:
        sums = parse_sums(f.read())
    want = sums.get(os.path.basename(path))
    if not want:
        return False, "SHA256SUMS beside the file does not list it: it was not checked."
    if sha256_file(path) != want:
        raise ReleaseError("%s does not match the SHA256SUMS beside it: the file is damaged."
                           % os.path.basename(path))
    return True, "Checked against SHA256SUMS."


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass
