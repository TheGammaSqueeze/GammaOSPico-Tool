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


def test_art_set_finds_accented_names(tmp_path, capsys):
    cardgen.rom(str(tmp_path / "card" / "roms" / "gba" / "Pokémon Émeraude (France).gba"))
    img = tmp_path / "c.png"
    Image.new("RGB", (300, 300), (9, 9, 9)).save(str(img))
    rc, out, err = run(capsys, "art", "set", str(tmp_path / "card"), "pokemon emeraude", str(img))
    assert rc == 0 and (tmp_path / "card" / "roms" / "gba" / "boxart" / "Pokémon Émeraude (France).ART").exists()


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


def _flash_120(tmp_path, console, model_key="kpa", **kw):
    from pico_tool.console import models
    model = models.by_key(model_key)
    kw.setdefault("tee_gammaos", console.tee_image("gammaos"))
    kw.setdefault("tee_stock", console.tee_image("stock"))
    return pkg.make_flash_package(str(tmp_path / ("gammaos-pico-%s-1.2.0-flash.zip" % model_key)), "1.2.0",
                                  gammaos=console.make_image("gammaos-pico", "1.2.0", model),
                                  stock=console.stock_image(model), console=model_key, **kw)


@pytest.mark.parametrize("model_key", ["kpa", "block"])
def test_console_flash_1_2_0_writes_the_secure_firmware_too(tmp_path, capsys, console, monkeypatch, model_key):
    from pico_tool.console import models
    model = models.by_key(model_key)
    console.state.model = model
    console.state.lk = {"lk_a": console.stock_image(model), "lk_b": console.stock_image(model)}
    fz = _flash_120(tmp_path, console, model_key)
    # the question says the secure firmware is written too
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    rc, out, err = run(capsys, "console", "flash", "--zip", fz)
    assert rc == 1 and not console.state.writes
    assert ("Write GammaOS Pico 1.2.0 to lk_a and lk_b of this %s, and its secure firmware to tee_a and tee_b?"
            % model.name) in err
    # this console's bootloader is older than 1.2.0: the backup made before the question has no tee
    assert "The secure firmware was not backed up" in out
    # and the tee is not read back
    rc, out, err = run(capsys, "console", "flash", "--zip", fz, "--yes")
    assert rc == 0, err
    assert [p for p, _h in console.state.writes] == ["tee_a", "tee_b", "lk_a", "lk_b"]
    assert console.state.tee["tee_a"] == console.state.tee["tee_b"] == console.tee_image("gammaos")
    assert "tee_a: written, not checked (the console's bootloader cannot read it back)" in out
    assert "lk_b: written and checked" in out
    assert "Done: GammaOS Pico 1.2.0 and its secure firmware are on the console." in out
    # now it runs 1.2.0: everything is read back
    console.state.tee_read = True
    rc, out, err = run(capsys, "console", "flash", "--zip", fz, "--yes")
    assert rc == 0 and "tee_a: written and checked" in out and "not checked" not in out
    # back to stock: the stock secure firmware with the stock bootloader
    del console.state.writes[:]
    rc, out, err = run(capsys, "console", "stock", "--zip", fz)
    assert rc == 1 and not console.state.writes
    assert ("Write the stock bootloader to lk_a and lk_b of this %s, and the stock secure firmware to tee_a and "
            "tee_b?" % model.name) in err
    rc, out, err = run(capsys, "console", "stock", "--zip", fz, "--yes")
    assert rc == 0, err
    assert [p for p, _h in console.state.writes] == ["tee_a", "tee_b", "lk_a", "lk_b"]
    assert console.state.tee["tee_a"] == console.state.tee["tee_b"] == console.tee_image("stock")
    assert console.state.lk["lk_a"] == console.state.lk["lk_b"] == console.stock_image(model)
    assert "Done: the stock bootloader and the stock secure firmware are on the console." in out


def test_console_old_package_says_nothing_about_the_secure_firmware(tmp_path, capsys, console, monkeypatch):
    fz = pkg.make_flash_package(str(tmp_path / "gammaos-pico-kpa-1.1.2-flash.zip"), "1.1.2")
    console.state.tee_read = True
    console.backup_lk(console.find_consoles()[0], console.state.backup_root, __import__("threading").Event(),
                      lambda f, m: None)
    tee_before = dict(console.state.tee)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    rc, out, err = run(capsys, "console", "flash", "--zip", fz)
    assert rc == 1 and "Write GammaOS Pico 1.0.0 to lk_a and lk_b of this Konkr Pocket Advance?" in err
    rc, out, err = run(capsys, "console", "flash", "--zip", fz, "--yes")
    assert rc == 0, err
    assert [p for p, _h in console.state.writes] == ["lk_a", "lk_b"] and console.state.tee == tee_before
    assert "tee" not in out and "secure firmware" not in out and "tee" not in err
    assert "Done: GammaOS Pico 1.0.0 is on the console." in out
    rc, out, err = run(capsys, "console", "stock", "--zip", fz, "--yes")
    assert rc == 0 and "secure firmware" not in out and console.state.tee == tee_before


def test_console_backup_before_a_write_gets_the_packages_images(tmp_path, capsys, console):
    """On a console that cannot send its data in one piece, these save reading what is in hand."""
    fz = _flash_120(tmp_path, console)
    assert run(capsys, "console", "flash", "--zip", fz, "--yes")[0] == 0
    cands = console.state.backup_candidates
    assert set(cands) == {"lk_a", "lk_b", "tee_a", "tee_b"}
    assert cands["lk_a"] == [console.make_image("gammaos-pico", "1.2.0"), console.stock_image()]
    assert cands["tee_b"] == [console.tee_image("gammaos"), console.tee_image("stock")]


def test_console_refuses_a_package_whose_tee_is_not_one(tmp_path, capsys, console):
    """An LK image where the secure firmware should be: refused before anything is written."""
    fz = _flash_120(tmp_path, console, tee_gammaos=console.make_image("gammaos-pico", "1.2.0"))
    rc, out, err = run(capsys, "console", "flash", "--zip", fz, "--yes")
    assert rc == 1 and not console.state.writes
    assert "secure firmware is refused, so nothing was written" in err


def test_console_backup_and_restore_with_the_secure_firmware(tmp_path, capsys, console):
    console.state.tee_read = True
    rc, out, err = run(capsys, "console", "backup")
    assert rc == 0 and "secure firmware" in out and "not backed up" not in out
    (b,) = console.list_backups(console.state.backup_root)
    rc, out, err = run(capsys, "console", "backups")
    assert "secure firmware (tee_a and tee_b)" in out
    fz = _flash_120(tmp_path, console)
    assert run(capsys, "console", "flash", "--zip", fz, "--yes")[0] == 0
    del console.state.writes[:]
    rc, out, err = run(capsys, "console", "restore", b["path"], "--yes")
    assert rc == 0, err
    assert [p for p, _h in console.state.writes] == ["tee_a", "tee_b", "lk_a", "lk_b"]
    assert console.state.tee["tee_a"] == console.tee_image("stock")
    assert "and its secure firmware are on the console" in out


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


def test_console_block_writes_both_lk_copies(tmp_path, capsys, console):
    from pico_tool import config
    from pico_tool.console.models import BLOCK
    console.state.model = BLOCK
    console.state.lk = {"lk_a": console.stock_image(BLOCK), "lk_b": b"never written by the factory"}
    fz = pkg.make_flash_package(str(tmp_path / "gammaos-pico-block-1.0.2-flash.zip"), "1.0.2", console="block")
    rc, out, err = run(capsys, "console", "detect")
    assert rc == 0 and "Konkr Pocket Block" in out and "BLOCK0001" in out
    assert config.Settings().get("console_model") == "block"          # remembered for the card's package
    rc, out, err = run(capsys, "console", "flash", "--zip", fz, "--yes")
    assert rc == 0, err
    assert [p for p, _h in console.state.writes] == ["lk_a", "lk_b"]
    assert console.state.lk["lk_b"].startswith(b"LKIMG:gammaos-pico")
    assert "lk_a: written and checked" in out and "lk_b: written and checked" in out
    (b,) = console.list_backups(console.state.backup_root)
    assert b["model"] == "block" and b["lk_b"]["kind"] == "unknown"       # backed up as the factory left it
    rc, out, err = run(capsys, "console", "backups")
    assert "Konkr Pocket Block" in out and "lk_b" in out
    # the backup's lk_b holds no bootloader, so only lk_a is put back
    rc, out, err = run(capsys, "console", "restore", b["path"], "--yes")
    assert rc == 0 and console.state.lk["lk_a"].startswith(b"STOCKBLK")
    assert [p for p, _h in console.state.writes] == ["lk_a", "lk_b", "lk_a"]
    rc, out, err = run(capsys, "console", "stock", "--zip", fz, "--yes")
    assert rc == 0 and [p for p, _h in console.state.writes] == ["lk_a", "lk_b", "lk_a", "lk_a", "lk_b"]
    assert console.state.lk["lk_b"].startswith(b"STOCKBLK")


def test_console_refuses_the_other_consoles_package_and_backup(tmp_path, capsys, console):
    from pico_tool.console.models import BLOCK
    kpa_zip = pkg.make_flash_package(str(tmp_path / "gammaos-pico-kpa-1.0.2-flash.zip"), "1.0.2")
    kpa_backup = console.backup_lk(console.find_consoles()[0], console.state.backup_root,
                                   __import__("threading").Event(), lambda f, m: None)
    console.state.model = BLOCK
    console.state.lk = {"lk_a": console.stock_image(BLOCK), "lk_b": b""}
    rc, out, err = run(capsys, "console", "flash", "--zip", kpa_zip, "--yes")
    assert rc == 1 and "gammaos-pico-block-<version>-flash.zip" in err
    rc, out, err = run(capsys, "console", "restore", kpa_backup, "--yes")
    assert rc == 1 and "Konkr Pocket Advance" in err
    img = tmp_path / "kpa.img"
    img.write_bytes(console.make_image())
    rc, out, err = run(capsys, "console", "flash", "--image", str(img), "--yes")
    assert rc == 1 and "not this Konkr Pocket Block" in err
    assert not console.state.writes


def test_setup_downloads_the_chosen_consoles_card(tmp_path, capsys, monkeypatch):
    from pico_tool.release import github
    picked = []
    rel = github.parse_release({"tag_name": "v1.0.2", "assets": [
        {"name": n, "browser_download_url": "https://x/" + n, "size": 1}
        for n in ("gammaos-pico-kpa-1.0.2-sdcard.zip", "gammaos-pico-block-1.0.2-sdcard.zip", "SHA256SUMS")]})
    monkeypatch.setattr(github, "latest", lambda timeout=20: rel)
    monkeypatch.setattr(github, "fetch_sums", lambda r: {})
    z = pkg.make_package(str(tmp_path / "p.zip"))

    def download(asset, sums, progress=None, cancel=None, dest_dir=None):
        picked.append(asset.name)
        return z
    monkeypatch.setattr(github, "download", download)
    (tmp_path / "card").mkdir()
    assert run(capsys, "setup", str(tmp_path / "card"), "--dry-run")[0] == 0
    assert run(capsys, "setup", str(tmp_path / "card"), "--dry-run", "--console", "block")[0] == 0
    from pico_tool import config
    config.Settings().set("console_model", "block")
    assert run(capsys, "update", str(tmp_path / "card"), "--dry-run")[0] == 0
    assert picked == ["gammaos-pico-kpa-1.0.2-sdcard.zip", "gammaos-pico-block-1.0.2-sdcard.zip",
                      "gammaos-pico-block-1.0.2-sdcard.zip"]


def test_check_network_never_shows_the_builtin_access(capsys, monkeypatch):
    from pico_tool import net
    from pico_tool.scrape import scraper
    secret = ("secretdevid42", "S3cr3tPassword", "secretsoft")
    monkeypatch.setattr(scraper, "builtin_credentials", lambda: secret)
    monkeypatch.setattr(net, "source", lambda: net.TRUSTSTORE)
    monkeypatch.setattr(net, "passed_over", lambda: [])
    seen = []

    def probe(url, timeout=20, user_agent=""):
        seen.append(url)
        return True, False, "HTTP 403"
    monkeypatch.setattr(net, "probe", probe)
    rc, out, err = run(capsys, "check-network")
    assert rc == 0
    assert "the system's trust store" in out
    assert "ScreenScraper (box art): reached (built-in access)" in out
    for s in secret + ("devpassword", "devid", "screenscraper.fr"):
        assert s not in out + err
    assert all("?" not in u for u in seen)                           # no address with a query is sent

    def failing(url, timeout=20, user_agent=""):
        return False, True, "certificate verify failed for https://api.screenscraper.fr/x?devpassword=S3cr3tPassword"
    monkeypatch.setattr(net, "probe", failing)
    rc, out, err = run(capsys, "check-network")
    assert rc == 1 and "secure connection failed" in out
    for s in secret + ("devpassword", "://"):
        assert s not in out + err


def test_pick_among_several_consoles():
    from pico_tool.console.models import BLOCK, KPA
    a = fake.ConsoleLink("fastboot", "KPA1", "a", None, KPA)
    b = fake.ConsoleLink("fastboot", "BLK1", "b", None, BLOCK)
    assert cli._pick([a], None) is a
    assert cli._pick([], None) is None
    assert cli._pick([a, b], "BLK1") is b
    assert cli._pick([a, b], "NOPE") is None
    with pytest.raises(cli.CliError) as e:
        cli._pick([a, b], None)
    assert "--serial" in str(e.value) and "Konkr Pocket Block BLK1" in str(e.value)
