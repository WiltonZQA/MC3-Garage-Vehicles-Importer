"""Extract files from the MC3 Dave archives.

Usage:
    python -I mc3tools/extract.py <folder_with_assets_x.dat> <output_dir> [filter ...]

Without a filter everything is extracted. Each filter is a substring of the internal path.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mc3tools.dave import DaveArchive  # noqa: E402
from mc3tools.xiso import safe_join  # noqa: E402


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    src, out, filters = argv[1], argv[2], [f.lower() for f in argv[3:]]
    for path in sorted(glob.glob(os.path.join(src, "assets_x.dat*"))):
        arc = DaveArchive(path)
        for e in arc.entries:
            if e.is_dir or (filters and not any(f in e.name.lower() for f in filters)):
                continue
            dest = safe_join(out, e.name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as w:
                w.write(arc.read(e))
            print(e.name)
        arc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
