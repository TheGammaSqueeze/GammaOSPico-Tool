import hashlib
import io
import json
import os
import socket
import time
import urllib.error

import pytest

import pkg
from pico_tool.release import flashpkg, github


class Resp(io.BytesIO):
    def __init__(self, data, headers=None):
        io.BytesIO.__init__(self, data)
        self.headers = headers or {}
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def serve(monkeypatch, routes):
    def urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        r = routes.get(url)
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return Resp(r, {"Content-Length": str(len(r))})
    monkeypatch.setattr(github.urllib.request, "urlopen", urlopen)


def release_json(card_url, sums_url):
    return json.dumps({"tag_name": "v2026.12.01", "name": "GammaOS Pico 2026.12.01", "html_url": "u", "assets": [
        {"name": "gammaos-pico-kpa-2026.12.01-sdcard.zip", "browser_download_url": card_url, "size": 10},
        {"name": "gammaos-pico-kpa-2026.12.01-flash.zip", "browser_download_url": "f", "size": 10},
        {"name": "SHA256SUMS", "browser_download_url": sums_url, "size": 10}]}).encode()


def test_latest_download_and_check(monkeypatch, tmp_path):
    data = open(pkg.make_package(str(tmp_path / "p.zip")), "rb").read()
    sums = "%s  gammaos-pico-kpa-2026.12.01-sdcard.zip\n%s  gammaos-pico-kpa-2026.12.01-flash.zip\n" % (
        hashlib.sha256(data).hexdigest(), "0" * 64)
    serve(monkeypatch, {github.API_LATEST: release_json("https://x/c", "https://x/s"), "https://x/c": data, "https://x/s": sums.encode()})
    rel = github.latest()
    assert rel.version == "2026.12.01" and rel.flash.name.endswith("-flash.zip")
    s = github.fetch_sums(rel)
    seen = []
    path = github.download(rel.card, s, progress=lambda d, t: seen.append((d, t)), dest_dir=str(tmp_path / "dl"))
    assert open(path, "rb").read() == data and seen[-1][0] == len(data)
    # a second time it is taken from the cache
    serve(monkeypatch, {})
    assert github.download(rel.card, s, dest_dir=str(tmp_path / "dl")) == path


def test_damaged_download_is_refused(monkeypatch, tmp_path):
    serve(monkeypatch, {github.API_LATEST: release_json("https://x/c", "https://x/s"), "https://x/c": b"broken",
                        "https://x/s": ("%s  gammaos-pico-kpa-2026.12.01-sdcard.zip\n" % ("a" * 64)).encode()})
    rel = github.latest()
    with pytest.raises(github.ReleaseError):
        github.download(rel.card, github.fetch_sums(rel), dest_dir=str(tmp_path))
    assert not os.listdir(str(tmp_path))


def test_no_release_offline_and_rate_limit(monkeypatch):
    serve(monkeypatch, {})
    with pytest.raises(github.ReleaseError, match="no release"):
        github.latest()
    serve(monkeypatch, {github.API_LATEST: urllib.error.URLError(OSError("Name or service not known"))})
    with pytest.raises(github.ReleaseError, match="could not be reached"):
        github.latest()
    serve(monkeypatch, {github.API_LATEST: socket.timeout()})
    with pytest.raises(github.ReleaseError, match="in time"):
        github.latest()
    hdrs = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 600)}
    serve(monkeypatch, {github.API_LATEST: urllib.error.HTTPError(github.API_LATEST, 403, "rl", hdrs, None)})
    with pytest.raises(github.ReleaseError, match="limit"):
        github.latest()


def test_release_without_card_package():
    with pytest.raises(github.ReleaseError):
        github.parse_release({"tag_name": "v1", "assets": [{"name": "other.zip"}]})


def test_parse_sums():
    s = github.parse_sums("%s  a.zip\n%s *dir/b.zip\nnonsense\n" % ("a" * 64, "B" * 64))
    assert s == {"a.zip": "a" * 64, "b.zip": "b" * 64}


def test_verify_local(tmp_path):
    p = tmp_path / "x-sdcard.zip"
    p.write_bytes(b"zip")
    assert github.verify_local(str(p))[0] is False
    (tmp_path / "SHA256SUMS").write_text("%s  x-sdcard.zip\n" % hashlib.sha256(b"zip").hexdigest())
    assert github.verify_local(str(p))[0] is True
    (tmp_path / "SHA256SUMS").write_text("%s  x-sdcard.zip\n" % ("0" * 64))
    with pytest.raises(github.ReleaseError):
        github.verify_local(str(p))


def test_flash_package(tmp_path):
    p = pkg.make_flash_package(str(tmp_path / "gammaos-pico-kpa-2026.12.01-flash.zip"))
    imgs = flashpkg.read_images(p)
    assert imgs["version"] == "2026.12.01" and imgs["gammaos"].startswith(b"LKIMG") and imgs["stock"].startswith(b"STOCK")
    bad = pkg.make_flash_package(str(tmp_path / "bad.zip"), bad_sum=True)
    with pytest.raises(flashpkg.FlashPackageError):
        flashpkg.read_images(bad)
    with pytest.raises(flashpkg.FlashPackageError):
        flashpkg.read_images(pkg.make_package(str(tmp_path / "card.zip")))


REAL = "/work/kpa/lk-mt6785/out/release"


@pytest.mark.skipif(not os.path.isdir(REAL), reason="no release build here")
def test_real_flash_package():
    zs = [f for f in os.listdir(REAL) if f.endswith("-flash.zip")]
    if not zs:
        pytest.skip("no flash package")
    imgs = flashpkg.read_images(os.path.join(REAL, zs[0]))
    assert len(imgs["gammaos"]) > 0 and len(imgs["stock"]) > 0
