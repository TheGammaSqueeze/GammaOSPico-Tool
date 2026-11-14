import io
import os
import struct
import sys
import unicodedata

import pytest
from PIL import Image

import cardgen
from pico_tool.art import artfile, store
from pico_tool.card.scanner import scan_card

GEN_BOXART = "/work/kpa/lk-mt6785/tools/ayaneo/gba"


def test_round_trip_exact_for_565_colours():
    im = Image.new("RGB", (16, 8))
    px = [((x * 8) & 0xF8, (y * 32) & 0xFC, (x * y * 8) & 0xF8) for y in range(8) for x in range(16)]
    im.putdata(px)
    data = artfile.encode(im)
    assert data[:4] == b"GART" and struct.unpack_from("<HHHH", data, 4) == (1, 0, 16, 8)
    assert len(data) == 12 + 16 * 8 * 2
    back = artfile.decode(data)
    assert back.size == (16, 8)
    # 5/6 bit channels come back expanded; re-encoding them gives the same file
    assert artfile.encode(back) == data


def test_sizes_follow_the_console():
    assert artfile.target_size(100, 50) == (100, 50)
    assert artfile.target_size(960, 640) == (224, 149)
    assert artfile.target_size(640, 960) == (149, 224)
    assert artfile.target_size(1000, 1) == (224, 1)
    assert artfile.target_size(300, 100) == (224, 75)


def test_transparency_lies_over_black():
    im = Image.new("RGBA", (4, 4), (255, 255, 255, 0))
    im.putpixel((0, 0), (255, 255, 255, 255))
    back = artfile.decode(artfile.encode(im))
    assert back.getpixel((1, 1)) == (0, 0, 0)
    assert back.getpixel((0, 0)) == (255, 255, 255)


def test_any_format_pillow_reads():
    for fmt in ("PNG", "JPEG", "BMP", "GIF", "WEBP"):
        buf = io.BytesIO()
        Image.new("RGB", (300, 200), (200, 30, 40)).save(buf, fmt)
        data = artfile.encode_bytes(buf.getvalue())
        assert artfile.parse_header(data) == (224, 149)


def test_bad_art_is_refused():
    for bad in (b"", b"GART", b"XART" + b"\0" * 20, b"GART\x01\x00\x01\x00\x02\x00\x02\x00" + b"\0" * 8,
                b"GART\x01\x00\x00\x00\x02\x00\x02\x00" + b"\0" * 4):
        assert not artfile.is_valid(bad)
    with pytest.raises(artfile.ArtError):
        artfile.encode_bytes(b"not a picture")


@pytest.mark.skipif(not os.path.isfile(os.path.join(GEN_BOXART, "gen_boxart.py")), reason="lk sources not here")
def test_same_bytes_as_gen_boxart():
    pytest.importorskip("numpy")
    sys.path.insert(0, GEN_BOXART)
    import gen_boxart
    im = Image.new("RGB", (500, 333))
    im.putdata([((x * 3) & 255, (y * 5) & 255, (x ^ y) & 255) for y in range(333) for x in range(500)])
    assert artfile.encode(im) == gen_boxart.convert_one(im, 224)


def _card(tmp_path):
    w = str(tmp_path / "w")
    cardgen.rom(str(tmp_path / "roms" / "gba" / "Pokémon (France).gba"))
    cardgen.rom(str(tmp_path / "roms" / "gba" / "Mario vs. Donkey Kong.gba"))
    cardgen.rom(str(tmp_path / "ES" / "ROMs" / "gba" / "Far Away.gba"))
    cardgen.make_7z(str(tmp_path / "roms" / "gba" / "Set.7z"), {"Café.gba": b"c" * 50, "Two.gba": b"t" * 50}, [], w)
    import zipfile
    with zipfile.ZipFile(str(tmp_path / "roms" / "gba" / "Raw.zip"), "w") as z:
        z.writestr("Naïve.gba", b"n" * 50)
        z.writestr("Plain.gba", b"p" * 50)
    cardgen.raw_zip(str(tmp_path / "roms" / "gba" / "Odd.zip"),
                    [(b"Bad\xff.gba", 0x800, b"b" * 50), (b"Fine.gba", 0, b"f" * 50)])
    return scan_card(str(tmp_path))


def art():
    return artfile.encode(Image.new("RGB", (40, 40), (1, 2, 3)))


def _index(tmp_path, res):
    return store.ArtIndex(str(tmp_path), legacy=res.legacy)


@pytest.mark.skipif(not cardgen.SEVENZ, reason="7z is not installed")
def test_write_find_remove(tmp_path):
    res = _card(tmp_path)
    by = {g.name: g for g in res.games}
    idx = _index(tmp_path, res)
    g = by["Pokémon (France)"]
    assert idx.find(g) == (store.NO_ART, None)
    p = store.write_art(idx, g, art())
    assert os.path.basename(p) == "Pokémon (France).ART"      # always under the game's own name
    assert idx.find(g)[0] == store.HAVE_ART
    # archive members in any script, by their own names
    assert os.path.basename(store.write_art(idx, by["Café"], art())) == "Café.ART"
    assert os.path.basename(store.write_art(idx, by["Naïve"], art())) == "Naïve.ART"
    assert os.path.basename(store.write_art(idx, by["Plain"], art())) == "Plain.ART"
    # bytes that are not UTF-8 can never be a file's name on the card
    bad = [x for x in res.games if x.name.startswith("Bad")][0]
    assert idx.find(bad)[0] == store.CANNOT
    # the old cut name is read, and replaced by the full one when a new cover is written
    m = by["Mario vs. Donkey Kong"]
    (tmp_path / "roms" / "gba" / "boxart" / "Mario vs.ART").write_bytes(art())
    idx.invalidate()
    assert idx.find(m)[0] == store.HAVE_ART
    store.write_art(idx, m, art())
    left = sorted(os.listdir(str(tmp_path / "roms" / "gba" / "boxart")))
    assert "Mario vs. Donkey Kong.ART" in left
    assert store.remove_art(idx, m) and idx.find(m)[0] == store.NO_ART
    assert not any(n.startswith("Mario") for n in os.listdir(str(tmp_path / "roms" / "gba" / "boxart")))


def test_old_underscore_art_is_found_only_when_not_shared(tmp_path):
    g = tmp_path / "roms" / "gba"
    for n in ("Pokémon", "ポケモン", "テトリス", "Café", "Caf_"):
        cardgen.rom(str(g / (n + ".gba")))
    box = g / "boxart"
    box.mkdir()
    (box / "Pok_mon.ART").write_bytes(art())          # an older version's name, this game's alone
    (box / "____.ART").write_bytes(art())             # shared by the two Japanese names
    res = scan_card(str(tmp_path))
    by = {x.name: x for x in res.games}
    idx = _index(tmp_path, res)
    st, path = idx.find(by["Pokémon"])
    assert st == store.HAVE_ART and path.endswith("Pok_mon.ART")
    assert idx.find(by["ポケモン"])[0] == store.NO_ART and idx.find(by["テトリス"])[0] == store.NO_ART
    # "Caf_" is a game's own name, so Café cannot take its cover
    (box / "Caf_.ART").write_bytes(art())
    idx.invalidate()
    assert idx.find(by["Caf_"])[0] == store.HAVE_ART and idx.find(by["Café"])[0] == store.NO_ART
    # without a full listing no underscore form is read
    assert store.ArtIndex(str(tmp_path)).find(by["Pokémon"])[0] == store.NO_ART
    # a new cover goes under the UTF-8 name, and the old one it replaces goes
    p = store.write_art(idx, by["Pokémon"], art())
    assert os.path.basename(p) == "Pokémon.ART"
    left = sorted(os.listdir(str(box)))
    assert "Pok_mon.ART" not in left and "____.ART" in left and "Caf_.ART" in left
    # the shared form is never removed for one of its games
    store.write_art(idx, by["ポケモン"], art())
    assert "____.ART" in os.listdir(str(box)) and "ポケモン.ART" in os.listdir(str(box))


def test_first_name_holding_anything_decides(tmp_path):
    g = tmp_path / "roms" / "gba"
    cardgen.rom(str(g / "Pokémon.gba"))
    box = g / "boxart"
    box.mkdir()
    (box / "Pokémon.ART").write_bytes(b"GART")        # too short: the old form is not tried after it
    (box / "Pok_mon.ART").write_bytes(art())
    res = scan_card(str(tmp_path))
    idx = _index(tmp_path, res)
    assert idx.find(res.games[0])[0] == store.NO_ART
    (box / "Pokémon.ART").write_bytes(b"")             # empty: as if it were not there
    idx.invalidate()
    assert idx.find(res.games[0])[0] == store.HAVE_ART


def test_decomposed_name_art(tmp_path):
    nfd = unicodedata.normalize("NFD", "Pokémon")
    cardgen.rom(str(tmp_path / "roms" / "gba" / (nfd + ".gba")))
    res = scan_card(str(tmp_path))
    idx = _index(tmp_path, res)
    p = store.write_art(idx, res.games[0], art())
    assert os.path.basename(p) == nfd + ".ART"
    assert idx.find(res.games[0]) == (store.HAVE_ART, p)
    # a composed name is another file for the console
    os.rename(p, os.path.join(os.path.dirname(p), "Pokémon.ART"))
    idx.invalidate()
    assert idx.find(res.games[0])[0] == store.NO_ART


@pytest.mark.skipif(not cardgen.SEVENZ, reason="7z is not installed")
def test_home_folder_and_waiting_png(tmp_path):
    res = _card(tmp_path)
    g = [x for x in res.games if x.name == "Far Away"][0]
    idx = _index(tmp_path, res)
    home = tmp_path / "roms" / "gba" / "boxart"
    home.mkdir(parents=True, exist_ok=True)
    (home / "far away.gba.png").write_bytes(b"png")
    idx.invalidate()
    assert idx.find(g)[0] == store.HAVE_PNG
    (home / "Far Away.ART").write_bytes(art())
    idx.invalidate()
    st, path = idx.find(g)
    assert st == store.HAVE_ART and path.endswith("Far Away.ART")
    # a new cover beside the game takes over; the home one stays for the console to fall back on
    p = store.write_art(idx, g, art())
    assert os.path.dirname(p).endswith(os.path.join("ROMs", "gba", "boxart"))
    assert idx.find(g)[1] == p
    gone = store.remove_art(idx, g)
    assert len(gone) == 3 and idx.find(g)[0] == store.NO_ART


def test_atomic_write_leaves_no_tmp(tmp_path):
    p = str(tmp_path / "a.ART")
    store.atomic_write(p, b"x")
    store.atomic_write(p, b"yy")
    assert open(p, "rb").read() == b"yy" and os.listdir(str(tmp_path)) == ["a.ART"]
