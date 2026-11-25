import hashlib
import io
import json
import os
import socket
import time
import urllib.error

import pytest

import pkg
from pico_tool.console.models import BLOCK, KPA
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
    monkeypatch.setattr(github.net, "urlopen", urlopen)


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
    assert rel.version == "2026.12.01" and rel.flash_for().name.endswith("-flash.zip")
    s = github.fetch_sums(rel)
    seen = []
    path = github.download(rel.card_for(), s, progress=lambda d, t: seen.append((d, t)), dest_dir=str(tmp_path / "dl"))
    assert open(path, "rb").read() == data and seen[-1][0] == len(data)
    # a second time it is taken from the cache
    serve(monkeypatch, {})
    assert github.download(rel.card_for(), s, dest_dir=str(tmp_path / "dl")) == path


def test_damaged_download_is_refused(monkeypatch, tmp_path):
    serve(monkeypatch, {github.API_LATEST: release_json("https://x/c", "https://x/s"), "https://x/c": b"broken",
                        "https://x/s": ("%s  gammaos-pico-kpa-2026.12.01-sdcard.zip\n" % ("a" * 64)).encode()})
    rel = github.latest()
    with pytest.raises(github.ReleaseError):
        github.download(rel.card_for(), github.fetch_sums(rel), dest_dir=str(tmp_path))
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
    assert imgs["model"] is KPA
    bad = pkg.make_flash_package(str(tmp_path / "bad.zip"), bad_sum=True)
    with pytest.raises(flashpkg.FlashPackageError):
        flashpkg.read_images(bad)
    with pytest.raises(flashpkg.FlashPackageError):
        flashpkg.read_images(pkg.make_package(str(tmp_path / "card.zip")))


def test_flash_package_with_the_secure_firmware(tmp_path):
    """From 1.2.0: tee_<console>_gammaos.img and tee_<console>_stock.img next to the LK images."""
    for console, model in (("kpa", KPA), ("block", BLOCK)):
        p = pkg.make_flash_package(str(tmp_path / ("gammaos-pico-%s-1.2.0-flash.zip" % console)), "1.2.0",
                                   console=console, tee_gammaos=b"TEE-NEW", tee_stock=b"TEE-STOCK")
        imgs = flashpkg.read_images(p, model)
        assert imgs["tee_gammaos"] == b"TEE-NEW" and imgs["tee_stock"] == b"TEE-STOCK"
        assert imgs["version"] == "1.2.0" and imgs["model"] is model and "gammaos" in imgs and "stock" in imgs
    # an older package has neither, and that is fine
    old = flashpkg.read_images(pkg.make_flash_package(str(tmp_path / "gammaos-pico-kpa-1.1.2-flash.zip"), "1.1.2"))
    assert "tee_gammaos" not in old and "tee_stock" not in old and "gammaos" in old
    # the secure firmware is checked against the package's SHA256SUMS like the LK
    bad = pkg.make_flash_package(str(tmp_path / "bad-tee.zip"), tee_gammaos=b"TEE-NEW", bad_sum=True)
    with pytest.raises(flashpkg.FlashPackageError):
        flashpkg.read_images(bad)


def test_block_flash_package(tmp_path):
    p = pkg.make_flash_package(str(tmp_path / "gammaos-pico-block-1.0.2-flash.zip"), "1.0.2", console="block")
    imgs = flashpkg.read_images(p)
    assert imgs["model"] is BLOCK and imgs["version"] == "1.0.2"
    assert imgs["gammaos"].startswith(b"LKIMG") and imgs["stock"].startswith(b"STOCKBLK")
    assert flashpkg.read_images(p, BLOCK)["model"] is BLOCK
    with pytest.raises(flashpkg.FlashPackageError, match="Konkr Pocket Block's flash package.*gammaos-pico-kpa-"):
        flashpkg.read_images(p, KPA)
    kp = pkg.make_flash_package(str(tmp_path / "k.zip"))
    with pytest.raises(flashpkg.FlashPackageError, match="Konkr Pocket Advance's flash package.*gammaos-pico-block-"):
        flashpkg.read_images(kp, BLOCK)


def test_renamed_flash_package_found_by_its_images(tmp_path):
    p = pkg.make_flash_package(str(tmp_path / "x.zip"), console="block", top=False)
    imgs = flashpkg.read_images(p)
    assert imgs["model"] is BLOCK and imgs["version"] is None


def test_not_a_flash_package_names_both(tmp_path):
    with pytest.raises(flashpkg.FlashPackageError) as e:
        flashpkg.read_images(pkg.make_package(str(tmp_path / "card.zip")))
    assert "gammaos-pico-kpa-<version>-flash.zip" in str(e.value)
    assert "gammaos-pico-block-<version>-flash.zip" in str(e.value)


def _assets(*names):
    return [{"name": n, "browser_download_url": "https://x/" + n, "size": 1} for n in names]


def test_release_with_both_consoles():
    rel = github.parse_release({"tag_name": "v1.0.2", "assets": _assets(
        "gammaos-pico-kpa-1.0.2-sdcard.zip", "gammaos-pico-kpa-1.0.2-flash.zip", "gammaos-pico-kpa-windows-tools.zip",
        "gammaos-pico-block-1.0.2-sdcard.zip", "gammaos-pico-block-1.0.2-flash.zip",
        "gammaos-pico-block-windows-tools.zip", "SHA256SUMS")})
    assert rel.version == "1.0.2" and rel.models() == [KPA, BLOCK]
    assert rel.card_for(KPA).name == "gammaos-pico-kpa-1.0.2-sdcard.zip"
    assert rel.card_for(BLOCK).name == "gammaos-pico-block-1.0.2-sdcard.zip"
    assert rel.flash_for(BLOCK).name == "gammaos-pico-block-1.0.2-flash.zip"
    assert rel.tools["block"].name == "gammaos-pico-block-windows-tools.zip"
    assert rel.card_for().name.startswith("gammaos-pico-kpa-")      # the Pocket Advance by default
    assert rel.sums.name == "SHA256SUMS"


def test_older_release_with_the_pocket_advance_only():
    rel = github.parse_release({"tag_name": "v1.0.1", "assets": _assets(
        "gammaos-pico-kpa-1.0.1-sdcard.zip", "gammaos-pico-kpa-1.0.1-flash.zip", "SHA256SUMS")})
    assert rel.version == "1.0.1" and rel.models() == [KPA]
    assert rel.flash_for(KPA).name == "gammaos-pico-kpa-1.0.1-flash.zip"
    with pytest.raises(github.ReleaseError, match="no microSD card package for the Konkr Pocket Block"):
        rel.card_for(BLOCK)
    with pytest.raises(github.ReleaseError, match="no flash package for the Konkr Pocket Block"):
        rel.flash_for(BLOCK)


def test_release_with_the_pocket_block_only():
    rel = github.parse_release({"tag_name": "v9", "assets": _assets("gammaos-pico-block-9-sdcard.zip")})
    assert rel.models() == [BLOCK] and rel.version == "9"
    with pytest.raises(github.ReleaseError):
        rel.card_for(KPA)


REAL = "/work/kpa/lk-mt6785/out/release"


@pytest.mark.skipif(not os.path.isdir(REAL), reason="no release build here")
def test_real_flash_package():
    zs = [f for f in os.listdir(REAL) if f.endswith("-flash.zip")]
    if not zs:
        pytest.skip("no flash package")
    imgs = flashpkg.read_images(os.path.join(REAL, zs[0]))
    assert len(imgs["gammaos"]) > 0 and len(imgs["stock"]) > 0
