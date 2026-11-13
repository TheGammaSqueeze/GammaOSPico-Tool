import io
import json
import threading

import pytest
from PIL import Image

import cardgen
from pico_tool.art import store
from pico_tool.card.scanner import scan_card
from pico_tool.card.systems import BY_KEY
from pico_tool.scrape import batch, scraper


def png(color=(10, 200, 30)):
    b = io.BytesIO()
    Image.new("RGB", (60, 80), color).save(b, "PNG")
    return b.getvalue()


def ss_json(region="us"):
    return json.dumps({"response": {"jeu": {"id": "1", "noms": [{"region": "wor", "text": "Game World"},
                                                                 {"region": "us", "text": "Game US"}],
                                            "medias": [{"type": "box-2D", "region": "jp", "url": "http://img/jp"},
                                                       {"type": "box-2D", "region": "us", "url": "http://img/us"},
                                                       {"type": "box-3D", "region": "wor", "url": "http://img/3d"}]}}}).encode()


def fake_get(routes, calls=None):
    def get(url, params=None, timeout=30):
        key = url
        if calls is not None:
            calls.append((url, dict(params or [])))
        r = routes.get(key)
        if callable(r):
            r = r()
        if r is None:
            return 404, b""
        return r
    return get


def test_screenscraper_match_picks_region_and_sends_crc(monkeypatch):
    calls = []
    monkeypatch.setattr(scraper, "_get", fake_get({scraper.SS_INFO_URL: (200, ss_json()),
                                                   "http://img/us": (200, png())}, calls))
    sc = scraper.ScreenScraper(devid="d", devpw="p", region="us")
    r = sc.match("Game", "Game (USA).gba", BY_KEY["gba"], 0x1234ABCD, 4096)
    assert r.matched and r.title == "Game US" and r.image_url == "http://img/us" and r.image
    params = calls[0][1]
    assert params["crc"] == "1234ABCD" and params["systemeid"] == "12" and params["romnom"] == "Game (USA).gba"
    assert params["romtaille"] == "4096"


def test_screenscraper_stops_on_quota_and_login(monkeypatch):
    sc = scraper.ScreenScraper(devid="d", devpw="p")
    for status, kind in ((430, scraper.StopScraping), (403, scraper.StopScraping), (423, scraper.StopScraping),
                         (429, scraper.Busy), (500, scraper.Busy)):
        monkeypatch.setattr(scraper, "_get", fake_get({scraper.SS_INFO_URL: (status, b"")}))
        with pytest.raises(kind):
            sc.match("G", "G.gba", BY_KEY["gba"])
    monkeypatch.setattr(scraper, "_get", fake_get({scraper.SS_INFO_URL: (200, "Le quota maximum est atteint".encode())}))
    with pytest.raises(scraper.StopScraping):
        sc.match("G", "G.gba", BY_KEY["gba"])
    monkeypatch.setattr(scraper, "_get", fake_get({}))
    assert not sc.match("G", "G.gba", BY_KEY["gba"]).matched


def test_without_credentials(monkeypatch):
    monkeypatch.setattr(scraper, "builtin_credentials", lambda: None)
    sc = scraper.ScreenScraper()
    assert not sc.ready
    with pytest.raises(scraper.StopScraping):
        sc.match("G", "G.gba", BY_KEY["gba"])
    assert not scraper.TheGamesDB("").ready


def test_thegamesdb(monkeypatch):
    games = json.dumps({"data": {"games": [{"id": 7, "game_title": "Other"}, {"id": 9, "game_title": "Advance Wars",
                                                                           "release_date": "2001-09-10"}]},
                        "remaining_monthly_allowance": 100}).encode()
    imgs = json.dumps({"data": {"base_url": {"original": "http://cdn/"},
                                "images": {"9": [{"type": "boxart", "side": "back", "filename": "b.png"},
                                                 {"type": "boxart", "side": "front", "filename": "f.png"}]}}}).encode()
    monkeypatch.setattr(scraper, "_get", fake_get({scraper.TGDB_BYNAME_URL: (200, games),
                                                   scraper.TGDB_IMAGES_URL: (200, imgs),
                                                   "http://cdn/f.png": (200, png())}))
    t = scraper.TheGamesDB("key")
    r = t.match("Advance Wars", "Advance Wars (USA).gba", BY_KEY["gba"])
    assert r.matched and r.image_url == "http://cdn/f.png"
    c = t.search("advance", BY_KEY["gba"])
    assert [x.title for x in c] == ["Other", "Advance Wars"] and c[1].year == "2001"
    monkeypatch.setattr(scraper, "_get", fake_get({scraper.TGDB_BYNAME_URL: (403, b"")}))
    with pytest.raises(scraper.StopScraping):
        t.search("x", BY_KEY["gba"])


def test_retry_after_busy(monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    n = {"i": 0}

    def call():
        n["i"] += 1
        if n["i"] < 3:
            raise scraper.Busy("busy")
        return "ok"
    assert scraper.with_retry(call, wait=0.0) == "ok" and n["i"] == 3
    with pytest.raises(scraper.StopScraping):
        scraper.with_retry(lambda: (_ for _ in ()).throw(scraper.StopScraping("q")))


class FakeScraper(object):
    uses_crc = True
    label = "Fake"
    ready = True

    def __init__(self, stop_after=None):
        self.seen = []
        self.stop_after = stop_after

    def match(self, title, rom_name, system, crc=None, size=None):
        self.seen.append((title, rom_name, crc))
        if self.stop_after is not None and len(self.seen) > self.stop_after:
            raise scraper.StopScraping("quota used up")
        r = scraper.Result()
        if title.startswith("Unknown"):
            return r
        r.matched, r.title, r.image = True, title, png()
        return r


def test_batch_writes_covers_where_the_console_reads(tmp_path):
    for n in ("Alpha (USA)", "Beta", "Unknown Thing"):
        cardgen.rom(str(tmp_path / "roms" / "gba" / (n + ".gba")))
    res = scan_card(str(tmp_path))
    idx = store.ArtIndex(str(tmp_path))
    fs = FakeScraper()
    events = []
    out = batch.run(res.games, fs, idx, progress=lambda i, n, g, m: events.append((i, n, m)))
    assert len(out.done) == 2 and len(out.missing) == 1 and not out.failed
    assert {t for t, _r, _c in fs.seen} == {"Alpha", "Beta", "Unknown Thing"}
    assert all(c is not None for _t, _r, c in fs.seen)          # the CRC was worked out for ScreenScraper
    assert idx.find(res.games[0])[0] == store.HAVE_ART
    # a second run skips what has covers; with overwrite it does them again
    again = batch.run(res.games, FakeScraper(), idx)
    assert len(again.skipped) == 2 and len(again.missing) == 1
    assert len(batch.run(res.games, FakeScraper(), idx, overwrite=True).done) == 2


def test_batch_stops_and_cancels(tmp_path):
    for n in ("A", "B", "C", "D"):
        cardgen.rom(str(tmp_path / "roms" / "gba" / (n + ".gba")))
    res = scan_card(str(tmp_path))
    out = batch.run(res.games, FakeScraper(stop_after=1), store.ArtIndex(str(tmp_path)))
    assert len(out.done) == 1 and out.stopped == "quota used up"
    ev = threading.Event()
    ev.set()
    out = batch.run(res.games, FakeScraper(), store.ArtIndex(str(tmp_path)), cancel=ev)
    assert out.cancelled and not out.done
