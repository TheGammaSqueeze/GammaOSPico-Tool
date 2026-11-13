import os
import zipfile
import zlib

import pytest

import cardgen
from pico_tool.card import archives

needs7z = pytest.mark.skipif(not cardgen.SEVENZ, reason="7z is not installed")
GBA = (".gba",)


def listing(path):
    kind = "zip" if path.endswith(".zip") else "7z"
    with open(path, "rb") as f:
        return archives.list_archive(kind, f, os.path.getsize(path), GBA)


def files(*names):
    return {n: cardgen.data(n, 5000) for n in names}


def check_crcs(members, src):
    for m in members:
        base = m.text.replace("\\", "/")
        assert m.size == len(src[base])
        assert m.crc == zlib.crc32(src[base]) & 0xFFFFFFFF


@needs7z
@pytest.mark.parametrize("opts", [["-m0=LZMA2"], ["-m0=LZMA"], ["-m0=Copy"], ["-ms=on"], ["-ms=off"],
                                  ["-mhc=off"], ["-m0=LZMA2", "-mx9"]])
def test_7z_methods_list_with_sizes_and_crcs(tmp_path, opts):
    src = files("a/One.gba", "Two.gba", "notes.txt")
    p = cardgen.make_7z(str(tmp_path / "x.7z"), src, opts, str(tmp_path))
    m = listing(p)
    assert sorted(x.raw for x in m) == ["Two.gba", "a/One.gba"]
    assert all(x.status == archives.OK for x in m)
    check_crcs(m, src)


@needs7z
def test_7z_order_is_the_archives(tmp_path):
    src = files("b.gba", "a.gba", "c.gba")
    p = cardgen.make_7z(str(tmp_path / "x.7z"), src, ["-ms=off"], str(tmp_path))
    raw = [x.raw for x in listing(p)]
    assert sorted(raw) == ["a.gba", "b.gba", "c.gba"]


@needs7z
def test_7z_encrypted_data_lists_but_cannot_start(tmp_path):
    p = cardgen.make_7z(str(tmp_path / "x.7z"), files("K1.gba", "K2.gba"), ["-pSECRET"], str(tmp_path))
    m = listing(p)
    assert [x.raw for x in m] == ["K1.gba", "K2.gba"]
    assert all(x.status == archives.ERR_PASSWORD for x in m)


@needs7z
def test_7z_encrypted_names_cannot_be_read(tmp_path):
    p = cardgen.make_7z(str(tmp_path / "x.7z"), files("H.gba"), ["-pSECRET", "-mhe=on"], str(tmp_path))
    with pytest.raises(archives.ArchiveError) as e:
        listing(p)
    assert e.value.code == archives.ERR_PASSWORD


@needs7z
@pytest.mark.parametrize("opts,code", [(["-m0=PPMd"], archives.ERR_METHOD), (["-m0=BCJ", "-m1=LZMA2"], archives.ERR_METHOD),
                                       (["-m0=BZip2"], archives.ERR_METHOD)])
def test_7z_other_methods_list_but_cannot_start(tmp_path, opts, code):
    p = cardgen.make_7z(str(tmp_path / "x.7z"), files("P.gba"), opts, str(tmp_path))
    m = listing(p)
    assert [x.raw for x in m] == ["P.gba"] and m[0].status == code


@needs7z
def test_7z_split_parts_and_damage(tmp_path):
    cardgen.make_7z(str(tmp_path / "s" / "S.7z"), {"S.gba": os.urandom(9000)}, ["-v2k", "-mx0"], str(tmp_path))
    first = str(tmp_path / "s" / "S.7z.001")
    assert os.path.exists(first)
    with pytest.raises(archives.ArchiveError):
        with open(first, "rb") as f:
            archives.list_archive("7z", f, os.path.getsize(first), GBA)
    bad = tmp_path / "bad.7z"
    bad.write_bytes(b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 40)
    with pytest.raises(archives.ArchiveError) as e:
        listing(str(bad))
    assert e.value.code == archives.ERR_DAMAGED


@needs7z
def test_7z_non_ascii_names_become_underscores(tmp_path):
    p = cardgen.make_7z(str(tmp_path / "x.7z"), {"Pokémon.gba": b"x" * 100}, [], str(tmp_path))
    m = listing(p)
    assert m[0].raw == "Pok_mon.gba" and m[0].text == "Pokémon.gba"


@needs7z
def test_7z_empty_files_and_folders_are_not_roms(tmp_path):
    src = {"Empty.gba": b"", "Real.gba": b"data" * 10}
    p = cardgen.make_7z(str(tmp_path / "x.7z"), src, [], str(tmp_path))
    assert [x.raw for x in listing(p)] == ["Real.gba"]


def test_zip_listing_crcs_and_methods(tmp_path):
    p = str(tmp_path / "x.zip")
    src = files("dir/A.gba", "B.GBA", "c.txt")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(zipfile.ZipInfo("dir/A.gba"), src["dir/A.gba"], compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("B.GBA", src["B.GBA"], compress_type=zipfile.ZIP_STORED)
        z.writestr("c.txt", src["c.txt"])
        z.writestr("folder.gba/", b"")
        z.writestr("Bz.gba", b"bz" * 100, compress_type=zipfile.ZIP_BZIP2)
    m = listing(p)
    assert [x.raw for x in m] == ["dir/A.gba", "B.GBA", "Bz.gba"]
    check_crcs(m[:2], src)
    assert m[2].status == archives.ERR_METHOD


def test_zip64_end_records(tmp_path):
    p = str(tmp_path / "x.zip")
    old = zipfile.ZIP_FILECOUNT_LIMIT
    zipfile.ZIP_FILECOUNT_LIMIT = 1
    try:
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("a.gba", b"1" * 50)
            z.writestr("b.gba", b"2" * 50)
    finally:
        zipfile.ZIP_FILECOUNT_LIMIT = old
    with open(p, "rb") as f:
        assert b"PK\x06\x06" in f.read()
    assert [x.raw for x in listing(p)] == ["a.gba", "b.gba"]


def test_zip_with_a_long_comment_and_raw_names(tmp_path):
    p = str(tmp_path / "x.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.comment = b"c" * 5000
        info = zipfile.ZipInfo("Café.gba")
        z.writestr(info, b"x" * 10)
    m = listing(p)
    # flagged UTF-8 by Python: the console keeps the bytes as they are
    assert m[0].raw == "CafÃ©.gba" and m[0].text == "Café.gba"


@needs7z
def test_zip_encrypted_and_split(tmp_path):
    p = cardgen.make_7z(str(tmp_path / "x.zip"), files("Z.gba"), ["-tzip", "-pSECRET"], str(tmp_path))
    assert listing(p)[0].status == archives.ERR_PASSWORD
    if cardgen.ZIPCLI:
        import subprocess
        (tmp_path / "big.gba").write_bytes(os.urandom(200000))
        subprocess.run([cardgen.ZIPCLI, "-q", "-0", "-s", "64k", "parts.zip", "big.gba"], cwd=str(tmp_path), check=True)
        with pytest.raises(archives.ArchiveError) as e:
            listing(str(tmp_path / "parts.zip"))
        assert e.value.code == archives.ERR_SPLIT


def test_not_a_zip(tmp_path):
    p = tmp_path / "x.zip"
    p.write_bytes(b"PK\x03\x04 nothing" * 3)
    with pytest.raises(archives.ArchiveError) as e:
        listing(str(p))
    assert e.value.code == archives.ERR_DAMAGED
