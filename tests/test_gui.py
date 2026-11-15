"""The window driven end to end (under a display; run with xvfb-run on a headless system): the card
set up from a package, the games listed and covers set, the console backed up and updated through a
stand-in for the console part."""

import time
import unicodedata

import pytest

import app_fake_console as fake
import cardgen
import pkg

tk = pytest.importorskip("tkinter")


def _display():
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except tk.TclError:
        return False


pytestmark = pytest.mark.skipif(not _display(), reason="no display (run under xvfb-run)")


def pump(app, until, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.root.update()
        if until():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMMAOS_PICO_TOOL_HOME", str(tmp_path / "home"))
    from pico_tool import config
    from pico_tool.gui import widgets
    from pico_tool.gui.pages import console as cpage
    from pico_tool.release import github
    fake.state.__init__()
    fake.state.backup_root = str(tmp_path / "backups")
    monkeypatch.setattr(cpage, "con", fake)
    monkeypatch.setattr(github, "latest", lambda timeout=20: (_ for _ in ()).throw(github.ReleaseError("offline")))
    answers = {"ask": True, "dialog": True, "told": []}
    monkeypatch.setattr(widgets, "ask", lambda *a, **k: answers["ask"])
    monkeypatch.setattr(widgets, "tell", lambda app, title, text, kind="note": answers["told"].append((title, text)))

    def show(self, wait=True):
        self.update_idletasks()
        r = answers["dialog"]
        self.destroy()
        return r
    monkeypatch.setattr(widgets.Dialog, "show", show)
    st = config.Settings()
    st.update({"geometry": "1100x760+0+0", "auto_refresh": False})
    from pico_tool.gui.app import App
    a = App(settings=st)
    a.answers = answers
    yield a
    try:
        a.quit()
    except Exception:
        pass


def test_card_setup_games_and_covers(app, tmp_path, monkeypatch):
    from tkinter import filedialog
    from pico_tool.card import volumes
    from PIL import Image
    card = tmp_path / "card"
    card.mkdir()
    z = pkg.make_package(str(tmp_path / "gammaos-pico-kpa-2026.12.01-sdcard.zip"))
    cardgen.rom(str(card / "roms" / "snes" / "Mine (USA).sfc"))
    nfd = unicodedata.normalize("NFD", "Pokémon Émeraude (France)")
    cardgen.rom(str(card / "roms" / "gba" / (nfd + ".gba")))
    app.set_card(volumes.folder_volume(str(card)))
    cp = app.pages[0]
    assert pump(app, lambda: cp.built)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **k: z)
    cp.start(local=True)
    assert pump(app, lambda: not cp.busy and (card / "gammaos" / "menu.pack").exists())
    assert pump(app, lambda: app.card.has_pico)
    # the games: listed like the console lists them
    app.show_name("Games")
    gp = app.pages[1]
    assert pump(app, lambda: gp.result is not None)
    assert sorted(g.name for g in gp.result.games) == ["Collie Defense", "Mine (USA)", nfd, "Tobu"]
    # the search folds accents, a decomposed name like a composed one
    gp.query.set("POKÉMON emeraude")
    assert pump(app, lambda: [g.name for g in gp.list.items] == [nfd])
    gp.query.set("mine")
    assert pump(app, lambda: [g.name for g in gp.list.items] == ["Mine (USA)"])
    gp.list.select_index(0)
    img = tmp_path / "c.png"
    Image.new("RGB", (200, 280), (40, 90, 200)).save(str(img))
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **k: str(img))
    gp.cover_from_file()
    assert pump(app, lambda: (card / "roms" / "snes" / "boxart" / "Mine (USA).ART").exists())
    gp.remove_cover()
    assert pump(app, lambda: not (card / "roms" / "snes" / "boxart" / "Mine (USA).ART").exists())
    for name in ("Console", "Settings", "About", "Card"):
        app.show_name(name)
        app.root.update()


def test_console_backup_and_install(app, tmp_path):
    app.show_name("Console")
    cp = app.pages[2]
    assert pump(app, lambda: cp.link is not None)
    cp.do_backup()
    assert pump(app, lambda: not cp.busy and fake.list_backups(fake.state.backup_root))
    fz = pkg.make_flash_package(str(tmp_path / "gammaos-pico-kpa-2026.12.01-flash.zip"))
    cp._flash_source = lambda which: ("file", fz)
    cp.do_install()
    assert pump(app, lambda: not cp.busy and len(fake.state.writes) == 2)
    assert fake.state.lk["lk_a"].startswith(b"LKIMG:gammaos-pico")
    cp.do_stock()
    assert pump(app, lambda: not cp.busy and len(fake.state.writes) == 4)
    assert fake.state.lk["lk_b"].startswith(b"STOCK")


def test_install_backs_up_a_console_with_none(app, tmp_path):
    app.show_name("Console")
    cp = app.pages[2]
    assert pump(app, lambda: cp.link is not None)
    fz = pkg.make_flash_package(str(tmp_path / "f.zip"))
    cp._flash_source = lambda which: ("file", fz)
    cp.do_install()
    assert pump(app, lambda: not cp.busy and len(fake.state.writes) == 2)
    b = fake.list_backups(fake.state.backup_root)
    assert len(b) == 1 and b[0]["lk_a"]["kind"] == "stock"


def test_accent_change_rebuilds(app):
    app.set_accent("Crimson")
    assert app.pal.name == "Crimson"
    app.show_name("Settings")
    app.root.update()
    assert app.page.name == "Settings"


def test_block_console_install_and_restore(app, tmp_path):
    from pico_tool.console.models import BLOCK
    fake.state.model = BLOCK
    fake.state.lk = {"lk_a": fake.stock_image(BLOCK), "lk_b": b"left alone"}
    app.show_name("Console")
    cp = app.pages[2]
    assert pump(app, lambda: cp.link is not None)
    assert cp.link.model is BLOCK
    assert pump(app, lambda: app.console_model() is BLOCK)          # remembered for the card's package
    assert cp.first_model.get() == "block"
    # a Pocket Advance package is refused before anything is written
    kz = pkg.make_flash_package(str(tmp_path / "gammaos-pico-kpa-1.0.2-flash.zip"), "1.0.2")
    cp._flash_source = lambda which: ("file", kz)
    told = len(app.answers["told"])
    cp.do_install()
    assert pump(app, lambda: not cp.busy and len(app.answers["told"]) > told)
    assert "gammaos-pico-block-<version>-flash.zip" in app.answers["told"][-1][1]
    assert fake.state.writes == []
    # its own package: backed up first, then lk_a only
    bz = pkg.make_flash_package(str(tmp_path / "gammaos-pico-block-1.0.2-flash.zip"), "1.0.2", console="block")
    cp._flash_source = lambda which: ("file", bz)
    cp.do_install()
    assert pump(app, lambda: not cp.busy and len(fake.state.writes) == 1)
    assert [p for p, _h in fake.state.writes] == ["lk_a"] and fake.state.lk["lk_b"] == b"left alone"
    (b,) = fake.list_backups(fake.state.backup_root)
    assert b["model"] == "block"
    assert pump(app, lambda: cp.backups)
    app.answers["dialog"] = 0                                        # the restore dialog picks the first backup
    cp.do_restore()
    assert pump(app, lambda: not cp.busy and len(fake.state.writes) == 2)
    assert fake.state.lk["lk_a"].startswith(b"STOCKBLK")


def test_console_model_choice_is_shared(app):
    from pico_tool.console.models import BLOCK, KPA
    fake.state.present = False
    assert app.console_model() is KPA
    app.show_name("Settings")
    sp = app.page
    app.show_name("Console")
    cp = app.page
    assert "gammaos-pico-kpa-windows-tools.zip" in "".join(_texts(cp.first_box))
    app.set_console_model(BLOCK)
    app.root.update()
    assert sp.model.get() == "block" and cp.first_model.get() == "block"
    text = "".join(_texts(cp.first_box))
    assert "gammaos-pico-block-windows-tools.zip" in text and "MT6768_Android_scatter.txt" in text
    assert "only lk_a ticked" in text
    assert app.settings.get("console_model") == "block"


def _texts(w):
    out = []
    try:
        out.append(str(w.cget("text")))
    except Exception:
        pass
    for c in w.winfo_children():
        out.extend(_texts(c))
    return out
