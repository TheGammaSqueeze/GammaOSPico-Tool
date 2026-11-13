import json
import os
import sys

import pytest
from PIL import Image

import cardgen
import pkg
import app_fake_console as fake
from pico_tool import cli


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMMAOS_PICO_TOOL_HOME", str(tmp_path / "home"))


def run(capsys, *args):
    rc = cli.main(list(args))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_setup_update_and_scan(tmp_path, capsys):
    card = tmp_path / "card"
    card.mkdir()
    z = pkg.make_package(str(tmp_path / "p.zip"))
    rc, out, err = run(capsys, "setup", str(card), "--zip", z, "--dry-run")
    assert rc == 0 and "10 new" in out and not os.listdir(str(card))
    rc, out, err = run(capsys, "setup", str(card), "--zip", z, "--yes")
    assert rc == 0 and (card / "gammaos" / "menu.pack").exists()
    (card / "gammaos" / "settings.bin").write_bytes(b"mine")
    z2 = pkg.make_package(str(tmp_path / "p2.zip"), "2026.12.24", extra={"gammaos/settings.bin": b"x"})
    rc, out, err = run(capsys, "update", str(card), "--zip", z2, "--yes")
    assert rc == 0 and (card / "gammaos" / "settings.bin").read_bytes() == b"mine"
    rc, out, err = run(capsys, "scan", str(card), "--json")
    games = json.loads(out)
    assert {g["name"] for g in games} == {"Collie Defense", "Tobu"}
    rc, out, err = run(capsys, "scan", str(card), "--missing")
    assert "Tobu" in out and "Collie" not in out


def test_art_set_and_rm(tmp_path, capsys):
    cardgen.rom(str(tmp_path / "card" / "roms" / "gba" / "Hero (USA).gba"))
    img = tmp_path / "c.png"
    Image.new("RGB", (300, 300), (9, 9, 9)).save(str(img))
    rc, out, err = run(capsys, "art", "set", str(tmp_path / "card"), "hero", str(img))
    assert rc == 0 and (tmp_path / "card" / "roms" / "gba" / "boxart" / "Hero (USA).ART").exists()
    rc, out, err = run(capsys, "art", "rm", str(tmp_path / "card"), "Hero (USA)", "--system", "gba")
    assert rc == 0 and not (tmp_path / "card" / "roms" / "gba" / "boxart" / "Hero (USA).ART").exists()
    rc, out, err = run(capsys, "art", "rm", str(tmp_path / "card"), "nothing")
    assert rc == 1


def test_bios(tmp_path, capsys):
    (tmp_path / "card").mkdir()
    b = tmp_path / "b.bin"
    b.write_bytes(b"\0" * 100)
    assert run(capsys, "bios", str(tmp_path / "card"), str(b))[0] == 1
    b.write_bytes(b"\0" * 16384)
    assert run(capsys, "bios", str(tmp_path / "card"), str(b))[0] == 1          # unknown md5 needs --force
    assert run(capsys, "bios", str(tmp_path / "card"), str(b), "--force")[0] == 0
    assert (tmp_path / "card" / "bios" / "gba_bios.bin").exists()


@pytest.fixture
def console(tmp_path, monkeypatch):
    import pico_tool
    fake.state.__init__()
    fake.state.backup_root = str(tmp_path / "backups")
    monkeypatch.setitem(sys.modules, "pico_tool.console", fake)
    monkeypatch.setattr(pico_tool, "console", fake, raising=False)
    return fake


def test_console_flash_backs_up_first(tmp_path, capsys, console):
    fz = pkg.make_flash_package(str(tmp_path / "gammaos-pico-kpa-2026.12.01-flash.zip"))
    rc, out, err = run(capsys, "console", "detect")
    assert rc == 0 and "KPA0001" in out
    rc, out, err = run(capsys, "console", "flash", "--zip", fz, "--yes")
    assert rc == 0, err
    assert "backing up" in out.lower()
    assert [p for p, _h in console.state.writes] == ["lk_a", "lk_b"]
    backups = console.list_backups(console.state.backup_root)
    assert len(backups) == 1 and backups[0]["lk_a"]["kind"] == "stock"
    # a second install does not back up again
    rc, out, err = run(capsys, "console", "flash", "--zip", fz, "--yes")
    assert rc == 0 and "backing up" not in out.lower()
    # restore the backup, then the stock bootloader from the package
    rc, out, err = run(capsys, "console", "restore", backups[0]["path"], "--yes")
    assert rc == 0 and console.state.lk["lk_a"].startswith(b"STOCK")
    rc, out, err = run(capsys, "console", "stock", "--zip", fz, "--yes")
    assert rc == 0
    rc, out, err = run(capsys, "console", "backups")
    assert "KPA0001" in out


def test_console_refuses_bad_images(tmp_path, capsys, console):
    img = tmp_path / "x.img"
    img.write_bytes(b"garbage")
    rc, out, err = run(capsys, "console", "flash", "--image", str(img), "--yes")
    assert rc == 1 and not console.state.writes
    b = console.backup_lk(console.find_consoles()[0], console.state.backup_root, __import__("threading").Event(),
                          lambda f, m: None)
    with open(os.path.join(b, "lk_a.img"), "ab") as f:
        f.write(b"tampered")
    rc, out, err = run(capsys, "console", "restore", b, "--yes")
    assert rc == 1 and "damaged" in err and not console.state.writes


def test_console_without_yes_is_refused(tmp_path, capsys, console, monkeypatch):
    fz = pkg.make_flash_package(str(tmp_path / "f.zip"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    rc, out, err = run(capsys, "console", "flash", "--zip", fz)
    assert rc == 1 and not console.state.writes
