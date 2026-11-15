"""The fastboot client against the in-memory console, at the protocol level."""

import pytest

from console_fake import FakeConsole, FakeLink, PACKET
from pico_tool.console.fastboot import (FastbootFail, FastbootSession, LinkError, ProtocolError,
                                        Transport)


def session_for(console=None):
    console = console or FakeConsole()
    return console, FastbootSession(FakeLink(console))


def test_getvar_okay_and_fail():
    c, s = session_for()
    assert s.getvar("product") == "k85v1_64_kpa"
    assert s.getvar("version-gammaos-pico") == "1.0.0 abc1234"
    with pytest.raises(FastbootFail) as e:
        s.getvar("nope")
    assert "not found" in e.value.text.lower()
    assert s.getvar_or_none("nope") is None
    # a FAIL does not end the session
    assert s.getvar("serialno") == "N7KBVK6D99ZD6HPB"


def test_info_lines_are_collected():
    c, s = session_for()
    seen = []
    r = s.run("oem part-read:lk_a", on_info=seen.append)
    assert r.tag == "OKAY"
    assert r.infos == ["part lk_a size 0x200000 offset 0x0 len 0x100000"]
    assert seen == r.infos


def test_command_length_limit():
    c, s = session_for()
    with pytest.raises(ValueError):
        s.run("getvar:" + "x" * 60)
    with pytest.raises(ValueError):
        s.run("")


@pytest.mark.parametrize("size", [0, 1, 511, 512, 513, 4096, 300000, 0x200000])
def test_download_sizes(size):
    c, s = session_for()
    data = bytes((i * 31) & 0xFF for i in range(size))
    marks = []
    s.download(data, progress=lambda d, t: marks.append((d, t)))
    assert c.download == data
    assert marks[-1] == (size, size)


def test_download_low_battery_fail():
    c, s = session_for()
    c.low_battery = True
    with pytest.raises(FastbootFail) as e:
        s.download(b"x" * 1000)
    assert "low power" in e.value.text


@pytest.mark.parametrize("size", [1, 100, 511, 512, 513, 1024, 64512, 65536, 0x100000, 0x100000 - 4, 1165632 - 0x100000])
def test_upload_never_swallows_the_okay(size):
    c, s = session_for()
    c.staged = bytes((i * 7 + 3) & 0xFF for i in range(size))
    got = s.upload()
    assert got == c.staged
    # the session is still in step: the next command gets its own answer
    assert s.getvar("product") == "k85v1_64_kpa"


@pytest.mark.parametrize("size", [1, 511, 512, 513, 65536, 0x100000, 0x100000 - 4])
def test_upload_with_zero_length_packet_after_data(size):
    """The Pocket Block's LK ends a data stream that fills its last packet with a zero length
    packet before the OKAY: it is skipped, and the session stays in step."""
    c, s = session_for(FakeConsole(console="block"))
    c.staged = bytes((i * 5 + 1) & 0xFF for i in range(size))
    assert s.upload() == c.staged
    assert s.getvar("product") == "k68v1_64_block"


def test_endless_zero_length_packets_are_refused():
    c, s = session_for()
    for _ in range(10):
        c.out.append(b"")
    with pytest.raises(ProtocolError):
        s.run("getvar:product")
    assert s.broken


def test_upload_nothing_staged():
    c, s = session_for()
    with pytest.raises(FastbootFail):
        s.upload()


def test_upload_short_data_is_detected():
    c, s = session_for()
    c.staged = b"\x01" * 5000
    c.short_upload = True
    with pytest.raises((ProtocolError, LinkError)):
        s.upload()
    assert s.broken


def test_timeout_marks_session_broken():
    c, s = session_for()
    c.silent.add("getvar:product")
    with pytest.raises(LinkError) as e:
        s.getvar("product")
    assert e.value.kind == "timeout"
    assert s.broken
    with pytest.raises(ProtocolError):
        s.getvar("serialno")


def test_unplugged():
    c, s = session_for()
    c.unplug_on.add("getvar:")
    with pytest.raises(LinkError) as e:
        s.getvar("product")
    assert e.value.kind == "gone"


class ScriptedLink(Transport):
    """Replies from a fixed list, to feed the client malformed answers."""

    max_packet = PACKET

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def send(self, data, timeout_ms):
        self.sent.append(data)

    def recv(self, size, timeout_ms):
        if not self.replies:
            raise LinkError("timeout")
        return self.replies.pop(0)


@pytest.mark.parametrize("reply", [b"WHAT", b"OK", b"DATAzzzzzzzz", b"X" * 65])
def test_malformed_replies(reply):
    s = FastbootSession(ScriptedLink([reply]))
    with pytest.raises(ProtocolError):
        s.run("getvar:product")
    assert s.broken


def test_data_where_okay_expected():
    s = FastbootSession(ScriptedLink([b"DATA00000010"]))
    with pytest.raises(ProtocolError):
        s.run("getvar:product")


def test_download_size_mismatch():
    s = FastbootSession(ScriptedLink([b"DATA00000010"]))
    with pytest.raises(ProtocolError):
        s.download(b"x" * 32)


def test_text_lines_count_as_info():
    s = FastbootSession(ScriptedLink([b"TEXTone", b"INFOtwo", b"OKAYdone"]))
    r = s.run("oem something")
    assert r.infos == ["one", "two"] and r.text == "done"
