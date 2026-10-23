"""Reader for original Xbox XDVDFS disc images.

Usage:
    python -I mc3tools/xiso.py list <iso>
    python -I mc3tools/xiso.py extract <iso> <output_dir> [filter]
"""
import os
import struct
import sys

SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"
# Full redump images have the game partition at one of these offsets;
# trimmed .xiso images start at 0.
PARTITION_OFFSETS = (0, 0x18300000, 0x2080000, 0xFD90000)


def safe_join(root, internal):
    """Join a path read from a game file to `root`, refusing anything that escapes it."""
    parts = [p for p in internal.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts or any(":" in p for p in parts):
        raise ValueError("unsafe path in game file: %r" % internal)
    return os.path.join(root, *parts)


class XIso:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.base = None
        for off in PARTITION_OFFSETS:
            self.f.seek(off + 32 * SECTOR)
            if self.f.read(20) == MAGIC:
                self.base = off
                break
        if self.base is None:
            raise ValueError("XDVDFS header not found")
        root_sector, root_size = struct.unpack("<II", self.f.read(8))
        self.root = (root_sector, root_size)

    def close(self):
        self.f.close()

    def _read(self, sector, size, offset=0):
        self.f.seek(self.base + sector * SECTOR + offset)
        return self.f.read(size)

    def _walk_dir(self, sector, size, prefix):
        if size == 0:
            return
        data = self._read(sector, size)
        stack = [0]
        seen = set()
        while stack:
            off = stack.pop()
            if off in seen or off + 14 > len(data):
                continue
            seen.add(off)
            left, right, start, length, attr, nlen = struct.unpack_from("<HHIIBB", data, off)
            if left == 0xFFFF:  # padding
                continue
            name = data[off + 14:off + 14 + nlen].decode("latin-1")
            path = prefix + name
            if left:
                stack.append(left * 4)
            if right:
                stack.append(right * 4)
            if attr & 0x10:
                yield path + "/", start, length, True
                yield from self._walk_dir(start, length, path + "/")
            else:
                yield path, start, length, False

    def entries(self):
        yield from self._walk_dir(*self.root, "")

    def read_file(self, start, length):
        return self._read(start, length)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    cmd, iso_path = argv[1], argv[2]
    iso = XIso(iso_path)
    try:
        if cmd == "list":
            for path, start, length, is_dir in sorted(iso.entries()):
                if not is_dir:
                    print(f"{length:12d}  {path}")
        elif cmd == "extract":
            out = argv[3]
            filt = argv[4].lower() if len(argv) > 4 else None
            for path, start, length, is_dir in iso.entries():
                if is_dir or (filt and filt not in path.lower()):
                    continue
                dest = safe_join(out, path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as w:
                    remaining, sector = length, start
                    while remaining:
                        chunk = min(remaining, 64 * SECTOR * 16)
                        w.write(iso._read(sector, chunk))
                        sector += chunk // SECTOR
                        remaining -= chunk
                print(path)
        else:
            print(__doc__)
            return 1
    finally:
        iso.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
