"""Reader for Midnight Club 3 "Dave" archives (Angel / Rockstar San Diego).

Layout:
    0x000  char[4] "Dave" (packed names) or "DAVE" (plain-text names)
    0x004  u32 entry count
    0x008  u32 directory size
    0x00C  u32 name table size
    0x800  entries: u32 name_off, u32 data_off, u32 size, u32 packed_size
    0x800 + dir_size: name table

"Dave" names: four 6-bit characters in every 3 bytes (LSB first). If the
first code is >= 56, the first two codes tell how many characters to reuse
from the previous name: ((c1 & 31) << 3) | (c0 & 7).
Packed data is raw deflate.
"""
import struct
import zlib

CHARSET = "\0 #$()-./?0123456789_abcdefghijklmnopqrstuvwxyz~"


def _codes(names, off):
    while True:
        b0, b1, b2 = names[off], names[off + 1], names[off + 2]
        off += 3
        for c in (b0 & 63, (b0 >> 6) | ((b1 & 15) << 2), (b1 >> 4) | ((b2 & 3) << 4), b2 >> 2):
            if c == 0:
                return
            yield c


def _decode_packed(names, off, prev):
    codes = list(_codes(names, off))
    prefix = ""
    if codes and codes[0] >= 56:
        n = ((codes[1] & 31) << 3) | (codes[0] & 7)
        prefix = prev[:n]
        codes = codes[2:]
    # Code 48 appears in very few names (cutscenes) and has no known mapping;
    # it becomes "[48]" so the name stays unique.
    return prefix + "".join(CHARSET[c] if c < len(CHARSET) else f"[{c}]" for c in codes)


class Entry:
    __slots__ = ("name", "offset", "size", "packed_size")

    def __init__(self, name, offset, size, packed_size):
        self.name, self.offset, self.size, self.packed_size = name, offset, size, packed_size

    @property
    def is_dir(self):
        return self.name.endswith("/")


class DaveArchive:
    """Opens a Dave archive from a path or from an already open file.

    `base` allows reading an archive embedded in another file (e.g. inside the ISO).
    """

    def __init__(self, path=None, fileobj=None, base=0):
        self.path = path
        self.owns_file = fileobj is None
        self.f = open(path, "rb") if fileobj is None else fileobj
        self.base = base
        self.f.seek(base)
        magic, count, dir_size, names_size = struct.unpack("<4sIII", self.f.read(16))
        if magic not in (b"Dave", b"DAVE"):
            raise ValueError(f"{path}: not a Dave archive ({magic!r})")
        self.f.seek(base + 0x800)
        raw = [struct.unpack("<IIII", self.f.read(16)) for _ in range(count)]
        self.f.seek(base + 0x800 + dir_size)
        names = self.f.read(names_size)
        self.entries = []
        prev = ""
        for name_off, data_off, size, packed in raw:
            if magic == b"Dave":
                name = _decode_packed(names, name_off, prev)
            else:
                name = names[name_off:names.index(b"\0", name_off)].decode("latin-1")
            prev = name
            self.entries.append(Entry(name, data_off, size, packed))
        self.by_name = {e.name: e for e in self.entries}

    def close(self):
        if self.owns_file:
            self.f.close()

    def read(self, entry):
        if isinstance(entry, str):
            entry = self.by_name[entry]
        self.f.seek(self.base + entry.offset)
        data = self.f.read(entry.packed_size if entry.packed_size else entry.size)
        if entry.packed_size and entry.packed_size != entry.size:
            data = zlib.decompress(data, -15)
        return data
