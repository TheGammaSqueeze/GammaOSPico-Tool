"""What a .zip or .7z on the card holds, read the way the console reads it.

The console lists an archive in a system folder as the ROMs inside it: every file whose name ends
with one of the system's extensions, in the archive's order. When the archive cannot be read it is
listed once under its own name (and says why when it is started). These readers follow the same
rules and limits, and also give each ROM's size and CRC32 from the archive's own tables, so nothing
is unpacked to list a card.

ZIP: read from the end record and the central directory (ZIP64 too); names are kept as raw bytes.
7z: the signature header, then the (possibly LZMA packed) header tables, from the public 7z format
description; LZMA and LZMA2 headers are unpacked with the standard library's lzma module.
"""

import lzma
import struct
import zlib



# the console's reasons (rom_archive.h RA_ERR_*)
OK = 0
ERR_READ = -1
ERR_DAMAGED = -2
ERR_NOROM = -3
ERR_METHOD = -4
ERR_PASSWORD = -5
ERR_TOOBIG = -6
ERR_HUGE = -7
ERR_SPLIT = -8

REASONS = {
    ERR_READ: "This game could not be read from the card",
    ERR_DAMAGED: "This archive is damaged",
    ERR_NOROM: "No game was found in this archive",
    ERR_METHOD: "This archive uses a compression the console cannot open",
    ERR_PASSWORD: "This archive is password protected",
    ERR_TOOBIG: "This game is too large for this system",
    ERR_HUGE: "This archive is too large to open",
    ERR_SPLIT: "Archives split into parts are not supported",
}

SCRATCH = 4 << 20       # the console's memory for an archive's tables


class ArchiveError(Exception):
    def __init__(self, code):
        Exception.__init__(self, REASONS.get(code, "This game could not be started"))
        self.code = code


class Member(object):
    """One ROM inside an archive."""
    __slots__ = ("raw", "text", "crc", "size", "status")

    def __init__(self, raw, text, crc, size, status=OK):
        self.raw = raw          # the console's name for it (one char per byte / per unit rule)
        self.text = text        # the same name for the screen
        self.crc = crc          # CRC32 from the archive's tables, None when it has none
        self.size = size        # unpacked size
        self.status = status    # OK, or why the console would not start it

    def __repr__(self):
        return "<Member %r %s %s>" % (self.raw, self.size, self.status)


def list_archive(kind, f, size, exts):
    """The ROMs in an archive: a list of Member, or raises ArchiveError (the console's reason)."""
    if kind == "zip":
        return _zip_list(f, size, exts)
    return _sz_list(f, size, exts)


def _read(f, size, off, n):
    if off > size or n > size - off:
        raise ArchiveError(ERR_DAMAGED)
    try:
        f.seek(off)
        b = f.read(n)
    except OSError:
        raise ArchiveError(ERR_READ)
    if len(b) != n:
        raise ArchiveError(ERR_READ)
    return b


def _ext_match(name, exts):
    """ra_ext_match: the name ends with an extension; folders never (a trailing / or \\)."""
    if not name or name[-1] in "/\\":
        return False
    low = name.lower() if name.isascii() else "".join(
        chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in name)
    return any(len(low) > len(e) and low.endswith(e) for e in exts)


# ---- ZIP ---------------------------------------------------------------------------------------

def _u16(b, o):
    return b[o] | (b[o + 1] << 8)


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def _zip_list(f, size, exts):
    if size < 22:
        raise ArchiveError(ERR_DAMAGED)
    tail = min(size, 65557)
    t = min(size, 1024)
    eocd = -1
    while True:
        b = _read(f, size, size - t, t)
        i = t - 22
        while i >= 0:
            if _u32(b, i) == 0x06054B50 and i + 22 + _u16(b, i + 20) <= t:
                eocd = i
                break
            i -= 1
        if eocd >= 0 or t == tail:
            break
        t = tail
    if eocd < 0:
        raise ArchiveError(ERR_DAMAGED)
    if _u16(b, eocd + 4) not in (0, 0xFFFF) or _u16(b, eocd + 6) not in (0, 0xFFFF):
        raise ArchiveError(ERR_SPLIT)
    entries = _u16(b, eocd + 10)
    cd_size = _u32(b, eocd + 12)
    cd_off = _u32(b, eocd + 16)
    if entries == 0xFFFF or cd_size == 0xFFFFFFFF or cd_off == 0xFFFFFFFF:
        if eocd < 20 or _u32(b, eocd - 20) != 0x07064B50:
            raise ArchiveError(ERR_DAMAGED)
        if _u32(b, eocd - 16) != 0:
            raise ArchiveError(ERR_SPLIT)
        z64 = _read(f, size, _u64(b, eocd - 12), 56)
        if _u32(z64, 0) != 0x06064B50:
            raise ArchiveError(ERR_DAMAGED)
        if _u32(z64, 16) != 0 or _u32(z64, 20) != 0:
            raise ArchiveError(ERR_SPLIT)
        entries, cd_size, cd_off = _u64(z64, 32), _u64(z64, 40), _u64(z64, 48)
    if cd_size > SCRATCH:
        raise ArchiveError(ERR_HUGE)
    if cd_off > size or cd_size > size - cd_off:
        raise ArchiveError(ERR_DAMAGED)
    cd = _read(f, size, cd_off, cd_size)
    out = []
    i = 0
    while entries > 0:
        entries -= 1
        if i + 46 > cd_size or _u32(cd, i) != 0x02014B50:
            raise ArchiveError(ERR_DAMAGED)
        nlen, xlen, clen = _u16(cd, i + 28), _u16(cd, i + 30), _u16(cd, i + 32)
        if i + 46 + nlen + xlen + clen > cd_size:
            raise ArchiveError(ERR_DAMAGED)
        raw = bytes(cd[i + 46:i + 46 + min(nlen, 255)])
        name = raw.decode("latin-1")
        if _ext_match(name, exts):
            flags, method = _u16(cd, i + 8), _u16(cd, i + 10)
            crc, csize, usize = _u32(cd, i + 16), _u32(cd, i + 20), _u32(cd, i + 24)
            disk = _u16(cd, i + 34)
            x, xe = i + 46 + nlen, i + 46 + nlen + xlen
            loc = _u32(cd, i + 42)
            bad = False
            while x + 4 <= xe:
                hid, n = _u16(cd, x), _u16(cd, x + 2)
                if x + 4 + n > xe:
                    bad = True
                    break
                if hid == 0x0001:
                    v = x + 4
                    if usize == 0xFFFFFFFF and v + 8 <= x + 4 + n:
                        usize = _u64(cd, v)
                        v += 8
                    if csize == 0xFFFFFFFF and v + 8 <= x + 4 + n:
                        csize = _u64(cd, v)
                        v += 8
                    if loc == 0xFFFFFFFF and v + 8 <= x + 4 + n:
                        loc = _u64(cd, v)
                x += 4 + n
            if flags & 0x800:
                text = raw.decode("utf-8", "replace")
            else:
                text = raw.decode("cp437", "replace")
            status = OK
            if disk not in (0, 0xFFFF):
                status = ERR_SPLIT
            elif bad:
                status = ERR_DAMAGED
            elif flags & 0x41 or method == 99:
                status = ERR_PASSWORD
            elif method not in (0, 8):
                status = ERR_METHOD
            elif method == 0 and csize != usize:
                status = ERR_DAMAGED
            out.append(Member(name, text, crc, usize, status))
        i += 46 + nlen + xlen + clen
    return out


# ---- 7z ----------------------------------------------------------------------------------------

SZ_COPY, SZ_LZMA, SZ_LZMA2, SZ_OTHER, SZ_AES = range(5)
SZ_MAX_FOLD = 4096
SZ_MAX_PACK = 8192
_SIG = b"7z\xbc\xaf\x27\x1c"


class _Cur(object):
    """A reader over the header bytes; a read past the end marks it bad, as the console's does."""
    __slots__ = ("b", "p", "end", "bad")

    def __init__(self, b, p=0, end=None):
        self.b = b
        self.p = p
        self.end = len(b) if end is None else end
        self.bad = False

    def byte(self):
        if self.p >= self.end:
            self.bad = True
            return 0
        v = self.b[self.p]
        self.p += 1
        return v

    def num(self):
        first = self.byte()
        mask = 0x80
        v = 0
        for i in range(8):
            if not first & mask:
                return v | ((first & (mask - 1)) << (8 * i))
            v |= self.byte() << (8 * i)
            mask >>= 1
        return v

    def u32(self):
        return self.byte() | (self.byte() << 8) | (self.byte() << 16) | (self.byte() << 24)

    def skip(self, n):
        if n > self.end - self.p:
            self.bad = True
            self.p = self.end
        else:
            self.p += n

    def bits(self, n, all_byte):
        if all_byte and self.byte():
            return [1] * n
        out = []
        b = 0
        for i in range(n):
            if not i & 7:
                b = self.byte()
            out.append((b >> (7 - (i & 7))) & 1)
        return out


class _Folder(object):
    __slots__ = ("method", "props", "pack_first", "usize", "crc", "nsub")

    def __init__(self):
        self.method = SZ_OTHER
        self.props = b""
        self.pack_first = 0
        self.usize = 0
        self.crc = None
        self.nsub = 1


class _Streams(object):
    def __init__(self):
        self.pack_pos = 0
        self.pack_size = []
        self.fold = []
        self.sub_size = []
        self.sub_crc = []


def _coder_method(idb):
    if idb == b"\x00":
        return SZ_COPY
    if idb == b"\x21":
        return SZ_LZMA2
    if idb == b"\x03\x01\x01":
        return SZ_LZMA
    if idb == b"\x06\xf1\x07\x01":
        return SZ_AES
    return SZ_OTHER


def _pack_info(c, s):
    s.pack_pos = c.num()
    npack = c.num()
    if c.bad or npack > SZ_MAX_PACK:
        raise ArchiveError(ERR_HUGE)
    s.pack_size = [0] * npack
    while True:
        pid = c.byte()
        if pid == 0 or c.bad:
            break
        if pid == 0x09:
            s.pack_size = [c.num() for _ in range(npack)]
        elif pid == 0x0A:
            d = c.bits(npack, 1)
            for k in range(npack):
                if d[k]:
                    c.u32()
        else:
            c.skip(c.num())
    if c.bad:
        raise ArchiveError(ERR_DAMAGED)


def _folders(c, s):
    if c.byte() != 0x0B:
        raise ArchiveError(ERR_DAMAGED)
    nfold = c.num()
    if c.bad or nfold > SZ_MAX_FOLD:
        raise ArchiveError(ERR_HUGE)
    if c.byte() != 0:
        raise ArchiveError(ERR_METHOD)
    s.fold = []
    nout = []
    pack_next = 0
    for _ in range(nfold):
        fo = _Folder()
        ncoders = c.num()
        if c.bad or ncoders == 0 or ncoders > 32:
            raise ArchiveError(ERR_DAMAGED)
        tin = tout = 0
        aes = False
        for _j in range(ncoders):
            flag = c.byte()
            idlen = flag & 0x0F
            nin = no = 1
            if flag & 0x80:
                raise ArchiveError(ERR_METHOD)
            idb = bytes(c.byte() for _k in range(idlen))
            meth = _coder_method(idb if idlen <= 16 else b"")
            if meth == SZ_AES:
                aes = True
            if flag & 0x10:
                nin = c.num()
                no = c.num()
            if flag & 0x20:
                np_ = c.num()
                if ncoders == 1 and np_ <= 8:
                    fo.props = bytes(c.byte() for _k in range(np_))
                else:
                    c.skip(np_)
            if ncoders == 1 and nin == 1 and no == 1:
                fo.method = meth
            tin += nin
            tout += no
            if c.bad or tin > 64 or tout > 64:
                raise ArchiveError(ERR_DAMAGED)
        if aes:
            fo.method = SZ_AES
        if tout == 0:
            raise ArchiveError(ERR_DAMAGED)
        for _j in range(tout - 1):
            c.num()
            c.num()
        npacked = tin - (tout - 1)
        if npacked > 1:
            for _j in range(npacked):
                c.num()
        fo.pack_first = pack_next
        pack_next += npacked
        nout.append(tout)
        s.fold.append(fo)
    if pack_next > len(s.pack_size):
        raise ArchiveError(ERR_DAMAGED)
    if c.byte() != 0x0C:
        raise ArchiveError(ERR_DAMAGED)
    for i, fo in enumerate(s.fold):
        for j in range(nout[i]):
            v = c.num()
            if j + 1 == nout[i]:
                fo.usize = v
    while True:
        pid = c.byte()
        if pid == 0 or c.bad:
            break
        if pid == 0x0A:
            d = c.bits(len(s.fold), 1)
            for i, fo in enumerate(s.fold):
                if d[i]:
                    fo.crc = c.u32()
        else:
            c.skip(c.num())
    if c.bad:
        raise ArchiveError(ERR_DAMAGED)


def _substreams(c, s):
    pid = c.byte()
    if pid == 0x0D:
        for fo in s.fold:
            fo.nsub = c.num()
        pid = c.byte()
    total = 0
    for fo in s.fold:
        total += fo.nsub
        if total > (1 << 20):
            raise ArchiveError(ERR_HUGE)
    sizes = []
    for fo in s.fold:
        n = fo.nsub
        if not n:
            continue
        acc = 0
        for _j in range(n - 1):
            v = c.num() if pid == 0x09 else 0
            sizes.append(v)
            acc += v
        if acc > fo.usize:
            raise ArchiveError(ERR_DAMAGED)
        sizes.append(fo.usize - acc)
    if pid == 0x09:
        pid = c.byte()
    crcs = [None] * total
    ndig = 0
    k = 0
    for fo in s.fold:
        if fo.nsub == 1 and fo.crc is not None:
            crcs[k] = fo.crc
        else:
            ndig += fo.nsub
        k += fo.nsub
    while pid != 0 and not c.bad:
        if pid == 0x0A:
            d = c.bits(ndig, 1)
            k = 0
            di = 0
            for fo in s.fold:
                if fo.nsub == 1 and fo.crc is not None:
                    k += 1
                    continue
                for _j in range(fo.nsub):
                    if d[di]:
                        crcs[k] = c.u32()
                    k += 1
                    di += 1
        else:
            c.skip(c.num())
        pid = c.byte()
    if c.bad:
        raise ArchiveError(ERR_DAMAGED)
    s.sub_size = sizes
    s.sub_crc = crcs


def _streams(c):
    s = _Streams()
    have_sub = False
    while True:
        pid = c.byte()
        if pid == 0 or c.bad:
            break
        if pid == 0x06:
            _pack_info(c, s)
        elif pid == 0x07:
            _folders(c, s)
        elif pid == 0x08:
            _substreams(c, s)
            have_sub = True
        else:
            raise ArchiveError(ERR_DAMAGED)
    if c.bad:
        raise ArchiveError(ERR_DAMAGED)
    if not have_sub and s.fold:
        _substreams(_Cur(b"\x00"), s)
    return s


def _lzma_filters(method, props, want):
    """The lzma module's raw filter for a 7z coder, or raises (the console's answer)."""
    if method == SZ_LZMA:
        if len(props) < 5 or props[0] >= 9 * 5 * 5:
            raise ArchiveError(ERR_METHOD)
        d = props[0]
        lc, lp, pb = d % 9, (d // 9) % 5, d // 45
        if lc + lp > 8:     # more model than the console keeps
            raise ArchiveError(ERR_METHOD)
        dic = struct.unpack_from("<I", props, 1)[0]
        dic = max(4096, min(dic, max(want, 4096)))
        return [{"id": lzma.FILTER_LZMA1, "lc": lc, "lp": lp, "pb": pb, "dict_size": dic}]
    if not props or props[0] > 40:
        raise ArchiveError(ERR_METHOD)
    b = props[0]
    dic = 0xFFFFFFFF if b == 40 else (2 | (b & 1)) << (b // 2 + 11)
    dic = max(4096, min(dic, max(want, 4096)))
    return [{"id": lzma.FILTER_LZMA2, "dict_size": dic}]


def _folder_unpack(f, size, s, fi, want):
    fo = s.fold[fi]
    if fo.method == SZ_AES:
        raise ArchiveError(ERR_PASSWORD)
    if fo.method == SZ_OTHER:
        raise ArchiveError(ERR_METHOD)
    if fo.pack_first >= len(s.pack_size):
        raise ArchiveError(ERR_DAMAGED)
    at = 32 + s.pack_pos + sum(s.pack_size[:fo.pack_first])
    left = s.pack_size[fo.pack_first]
    if at > size or left > size - at:
        raise ArchiveError(ERR_DAMAGED)
    if fo.method == SZ_COPY:
        if want > left:
            raise ArchiveError(ERR_DAMAGED)
        return _read(f, size, at, want)
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=_lzma_filters(fo.method, fo.props, want))
    out = bytearray()
    try:
        f.seek(at)
    except OSError:
        raise ArchiveError(ERR_READ)
    while len(out) < want:
        if dec.needs_input:
            n = min(left, 256 << 10)
            if not n:
                raise ArchiveError(ERR_DAMAGED)
            piece = f.read(n)
            if len(piece) != n:
                raise ArchiveError(ERR_READ)
            left -= n
        else:
            piece = b""
        try:
            out += dec.decompress(piece, max_length=want - len(out))
        except lzma.LZMAError:
            raise ArchiveError(ERR_DAMAGED)
        if dec.eof and len(out) < want:
            raise ArchiveError(ERR_DAMAGED)
    return bytes(out)


def _sz_list(f, size, exts):
    h = _read(f, size, 0, 32) if size >= 32 else None
    if h is None:
        raise ArchiveError(ERR_DAMAGED)
    if h[:6] != _SIG or h[6] != 0:
        raise ArchiveError(ERR_DAMAGED)
    if zlib.crc32(h[12:32]) & 0xFFFFFFFF != _u32(h, 8):
        raise ArchiveError(ERR_DAMAGED)
    next_off, next_size = _u64(h, 12), _u64(h, 20)
    if next_size == 0:
        raise ArchiveError(ERR_NOROM)
    if next_size > SCRATCH // 4:
        raise ArchiveError(ERR_HUGE)
    if 32 + next_off > size or next_size > size - 32 - next_off:
        raise ArchiveError(ERR_DAMAGED)
    tab = _read(f, size, 32 + next_off, next_size)
    if zlib.crc32(tab) & 0xFFFFFFFF != _u32(h, 28):
        raise ArchiveError(ERR_DAMAGED)
    c = _Cur(tab)
    rnd = 0
    while True:
        pid = c.byte()
        if pid == 0x01:
            break
        if pid != 0x17 or rnd > 3:
            raise ArchiveError(ERR_DAMAGED)
        st = _streams(c)
        if not st.fold:
            raise ArchiveError(ERR_DAMAGED)
        if st.fold[0].usize > SCRATCH // 4:
            raise ArchiveError(ERR_HUGE)
        n = st.fold[0].usize
        un = _folder_unpack(f, size, st, 0, n)
        if st.fold[0].crc is not None and zlib.crc32(un) & 0xFFFFFFFF != st.fold[0].crc:
            raise ArchiveError(ERR_DAMAGED)
        c = _Cur(un)
        rnd += 1
    st = _Streams()
    nfiles = 0
    empty = []
    names = None
    while True:
        pid = c.byte()
        if pid == 0 or c.bad:
            break
        if pid == 0x02:
            while True:
                t = c.byte()
                if t == 0 or c.bad:
                    break
                c.skip(c.num())
        elif pid == 0x03:
            raise ArchiveError(ERR_METHOD)
        elif pid == 0x04:
            st = _streams(c)
        elif pid == 0x05:
            nfiles = c.num()
            if c.bad or nfiles > (1 << 20):
                raise ArchiveError(ERR_HUGE)
            empty = [0] * nfiles
            while True:
                t = c.num()
                if t == 0 or c.bad:
                    break
                n = c.num()
                at = c.p
                if n > c.end - c.p:
                    raise ArchiveError(ERR_DAMAGED)
                if t == 0x0E:
                    empty = _Cur(c.b, at, at + n).bits(nfiles, 0)
                elif t == 0x11:
                    if n < 1 or c.b[at] != 0:
                        raise ArchiveError(ERR_METHOD)
                    names = (at + 1, at + n)
                c.skip(n)
        else:
            raise ArchiveError(ERR_DAMAGED)
    if c.bad:
        raise ArchiveError(ERR_DAMAGED)
    if names is None:
        raise ArchiveError(ERR_NOROM)
    buf = c.b
    q, qend = names
    stream = 0
    out = []
    # the folder of each substream, for its method
    fold_of = []
    for fi, fo in enumerate(st.fold):
        fold_of.extend([fi] * fo.nsub)
    nsub = len(st.sub_size)
    for i in range(nfiles):
        units = []
        while q + 1 < qend and (buf[q] | buf[q + 1]):
            units.append(buf[q] | (buf[q + 1] << 8))
            q += 2
        if q + 1 >= qend:
            raise ArchiveError(ERR_DAMAGED)
        q += 2
        if empty[i]:
            continue
        if stream >= nsub:
            raise ArchiveError(ERR_DAMAGED)
        text = b"".join(struct.pack("<H", u) for u in units).decode("utf-16-le", "replace")
        raw = "".join(chr(u) if u < 0x80 else "_" for u in units[:255])
        if _ext_match(raw, exts):
            fo = st.fold[fold_of[stream]]
            status = OK
            if fo.method == SZ_AES:
                status = ERR_PASSWORD
            elif fo.method == SZ_OTHER:
                status = ERR_METHOD
            out.append(Member(raw, text, st.sub_crc[stream], st.sub_size[stream], status))
        stream += 1
    return out
