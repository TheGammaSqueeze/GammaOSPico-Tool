import os
import zipfile

import pytest

import pkg
from pico_tool.card import layout
from pico_tool.release import install


def tree(root):
    out = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root).replace(os.sep, "/")] = open(p, "rb").read()
    return out


def test_new_card(tmp_path):
    z = pkg.make_package(str(tmp_path / "p.zip"))
    card = tmp_path / "card"
    card.mkdir()
    p = install.plan(z, str(card))
    assert p.version == "2026.12.01" and p.top == "gammaos-pico-kpa-2026.12.01-sdcard/"
    assert len(p.of(install.NEW)) == 10 and not p.of(install.REPLACE)
    written = install.apply(p)
    assert len(written) == 10
    t = tree(str(card))
    assert t["gammaos/menu.pack"] == b"MENU-NEW" and "roms/gba/Collie Defense.gba" in t
    for s in ("gb", "gbc", "gba", "snes", "genesis", "sms", "gg", "sg"):
        assert os.path.isdir(str(card / "roms" / s))
    assert layout.has_pico(str(card)) and layout.card_version(str(card)) == "2026.12.01"
    assert not any(k.endswith(".tmp") for k in t)


def test_update_replaces_firmware_and_roots_keeps_network_and_ra_data(tmp_path):
    """1.1: bios/connsys/ and bios/certs/ are the release's (replaced), gammaos/net/ and gammaos/ra/
    are the console's own data (kept), although bios/ and gammaos/ otherwise go the other way."""
    card = tmp_path / "card"
    old = pkg.make_package(str(tmp_path / "old.zip"), "1.0.2", extra={
        "bios/connsys/WIFI_RAM_CODE_soc1_0_2a_1.bin": b"FW-OLD", "bios/certs/roots.der": b"ROOTS-OLD"})
    install.apply(install.plan(old, str(card.mkdir() or card)))
    mine = {"gammaos/net/wifi.dat": b"saved networks", "gammaos/net/lrdb/1.db": b"lists",
            "gammaos/ra/account.dat": b"account", "gammaos/ra/queue.dat": b"unlocks"}
    for rel, data in mine.items():
        p = card / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    new = pkg.make_package(str(tmp_path / "new.zip"), "1.1.0", extra={
        "bios/connsys/WIFI_RAM_CODE_soc1_0_2a_1.bin": b"FW-NEW", "bios/certs/roots.der": b"ROOTS-NEW",
        "gammaos/ra/account.dat": b"never"})
    p = install.plan(new, str(card))
    acts = {i.rel: i.action for i in p.items}
    assert acts["bios/connsys/WIFI_RAM_CODE_soc1_0_2a_1.bin"] == install.REPLACE
    assert acts["bios/certs/roots.der"] == install.REPLACE
    assert acts["gammaos/ra/account.dat"] == install.KEEP
    install.apply(p)
    t = tree(str(card))
    assert t["bios/connsys/WIFI_RAM_CODE_soc1_0_2a_1.bin"] == b"FW-NEW" and t["bios/certs/roots.der"] == b"ROOTS-NEW"
    for rel, data in mine.items():
        assert t[rel] == data, rel
    for rel in ("gammaos/net/wifi.dat", "gammaos/ra/queue.dat", "gammaos/ra/b/1.bdg", "bios/gba_bios.bin"):
        assert layout.is_user_path(rel), rel
    for rel in ("bios/connsys/x.bin", "BIOS/Certs/roots.der", "gammaos/menu.pack"):
        assert not layout.is_user_path(rel), rel


def test_update_keeps_the_users_files(tmp_path):
    card = tmp_path / "card"
    old = pkg.make_package(str(tmp_path / "old.zip"), "2026.11.01", menu=b"MENU-OLD")
    install.apply(install.plan(old, str(card.mkdir() or card)))
    mine = {
        "gammaos/settings.bin": b"S", "gammaos/folders.txt": b"F", "gammaos/playtime.txt": b"P",
        "gammaos/recent.txt": b"R", "gammaos/archives.idx": b"A", "gammaos/cart_header.bin": b"C",
        "gammaos/blackbox.bin": b"B", "gammaos/boot.cfg": b"boot=android\n", "saves/gba/x.sav": b"save",
        "states/gba/x.st0": b"state", "bios/gba_bios.bin": b"bios", "bios/README.txt": b"mine",
        "roms/gba/My Game.gba": b"game", "roms/gba/boxart/My Game.ART": b"art",
        "roms/gba/boxart/Collie Defense.ART": b"my cover", "Elsewhere/cover.art": b"a",
    }
    for rel, data in mine.items():
        p = card / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    os.remove(str(card / "roms" / "gb" / "Tobu.gb"))          # a demo game the user deleted
    new = pkg.make_package(str(tmp_path / "new.zip"), "2026.12.01", extra={
        "gammaos/settings.bin": b"DEFAULTS", "roms/gba/My Game.gba": b"never", "gammaos/new.pack": b"N"})
    p = install.plan(new, str(card), demo_games=False)
    acts = {i.rel: i.action for i in p.items}
    assert acts["gammaos/menu.pack"] == install.REPLACE
    assert acts["gammaos/new.pack"] == install.NEW
    assert acts["gammaos/lang-fr-960x640.pack"] == install.SAME
    assert acts["gammaos/settings.bin"] == install.KEEP and acts["gammaos/boot.cfg"] == install.KEEP
    assert acts["roms/gba/My Game.gba"] == install.KEEP and acts["bios/README.txt"] == install.KEEP
    assert acts["roms/gb/Tobu.gb"] == install.SKIP
    install.apply(p)
    t = tree(str(card))
    for rel, data in mine.items():
        assert t[rel] == data, rel
    assert t["gammaos/menu.pack"] == b"MENU-NEW" and t["gammaos/new.pack"] == b"N"
    assert "roms/gb/Tobu.gb" not in t
    # with the demo games, only the missing ones come back
    p2 = install.plan(new, str(card), demo_games=True)
    assert {i.rel for i in p2.of(install.NEW)} == {"roms/gb/Tobu.gb"}


def test_card_with_capital_names(tmp_path):
    card = tmp_path / "card"
    (card / "GAMMAOS").mkdir(parents=True)
    (card / "GAMMAOS" / "SETTINGS.BIN").write_bytes(b"mine")
    (card / "GAMMAOS" / "MENU.PACK").write_bytes(b"old")
    p = install.plan(pkg.make_package(str(tmp_path / "p.zip"), extra={"gammaos/settings.bin": b"no"}), str(card))
    acts = {i.rel: i.action for i in p.items}
    assert acts["gammaos/settings.bin"] == install.KEEP and acts["gammaos/menu.pack"] == install.REPLACE
    install.apply(p)
    assert (card / "GAMMAOS" / "MENU.PACK").read_bytes() == b"MENU-NEW"
    assert (card / "GAMMAOS" / "SETTINGS.BIN").read_bytes() == b"mine"


@pytest.mark.parametrize("bad", ["../evil.txt", "pkg/../../evil.txt", "/etc/evil", "C:/evil", "pkg/a/..\\..\\..\\evil"])
def test_zip_slip_is_refused(tmp_path, bad):
    z = str(tmp_path / "p.zip")
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("pkg/gammaos/menu.pack", b"m")
        zz.writestr(bad, b"x")
    (tmp_path / "card").mkdir()
    with pytest.raises(install.InstallError):
        install.plan(z, str(tmp_path / "card"))
    assert not (tmp_path / "evil.txt").exists()


def test_links_and_wrong_packages_are_refused(tmp_path):
    z = str(tmp_path / "p.zip")
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("pkg/gammaos/menu.pack", b"m")
        info = zipfile.ZipInfo("pkg/link")
        info.external_attr = (0o120777 << 16)
        zz.writestr(info, "/etc/passwd")
    (tmp_path / "card").mkdir()
    with pytest.raises(install.InstallError):
        install.plan(z, str(tmp_path / "card"))
    other = str(tmp_path / "o.zip")
    with zipfile.ZipFile(other, "w") as zz:
        zz.writestr("readme.txt", b"x")
    with pytest.raises(install.InstallError):
        install.plan(other, str(tmp_path / "card"))
    (tmp_path / "junk.zip").write_bytes(b"not a zip")
    with pytest.raises(install.InstallError):
        install.plan(str(tmp_path / "junk.zip"), str(tmp_path / "card"))


def test_no_room(tmp_path, monkeypatch):
    import collections
    import shutil
    card = tmp_path / "card"
    card.mkdir()
    p = install.plan(pkg.make_package(str(tmp_path / "p.zip")), str(card))
    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(100, 100, 10))
    with pytest.raises(install.InstallError):
        install.apply(p)
    assert not os.listdir(str(card))


def test_cancel_keeps_finished_files(tmp_path):
    import threading
    card = tmp_path / "card"
    card.mkdir()
    p = install.plan(pkg.make_package(str(tmp_path / "p.zip")), str(card))
    ev = threading.Event()
    ev.set()
    with pytest.raises(install.InstallError):
        install.apply(p, cancel=ev)


REAL = "/work/kpa/lk-mt6785/out/release"


@pytest.mark.skipif(not os.path.isdir(REAL), reason="no release build here")
def test_real_release_package(tmp_path):
    zs = [f for f in os.listdir(REAL) if f.endswith("-sdcard.zip")]
    if not zs:
        pytest.skip("no card package")
    p = install.plan(os.path.join(REAL, zs[0]), str(tmp_path))
    assert p.version and p.of(install.NEW)
    assert any(i.rel == "gammaos/menu.pack" for i in p.items)


def test_bios_checks(tmp_path, monkeypatch):
    card = tmp_path / "card"
    card.mkdir()
    wrong = tmp_path / "small.bin"
    wrong.write_bytes(b"x" * 100)
    assert not layout.check_bios(str(wrong)).ok
    with pytest.raises(ValueError):
        layout.install_bios(str(card), str(wrong))
    odd = tmp_path / "odd.bin"
    odd.write_bytes(b"\1" * 16384)
    c = layout.check_bios(str(odd))
    assert c.ok and not c.known
    import hashlib
    monkeypatch.setattr(layout, "BIOS_MD5", hashlib.md5(b"\1" * 16384).hexdigest())
    assert layout.check_bios(str(odd)).known
    dest = layout.install_bios(str(card), str(odd))
    assert dest.endswith(os.path.join("bios", "gba_bios.bin")) and layout.installed_bios(str(card)) == dest
