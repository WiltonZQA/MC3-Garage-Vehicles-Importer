"""Access to the game files: Xbox ISO, folder with assets_x.dat*, or a loose .xbck."""
import glob
import os

from .dave import DaveArchive
from .xiso import XIso

CITIES = ("sd", "atlanta", "detroit", "tokyo")
TIMES = ("dawn", "dusk", "midnight")
WEATHERS = ("clear", "cloudy", "rainy")


def garage_entry(city, time="midnight", weather="clear"):
    return "resources/prop/%s_%s_%s_garage_props.xbck" % (city, time, weather)


class GameFiles:
    """Opens every Dave archive of the game, whether from an ISO or a folder."""

    def __init__(self, path):
        self.archives = []
        self._iso = None
        if os.path.isfile(path) and path.lower().endswith(".iso"):
            self._iso = XIso(path)
            for name, start, length, is_dir in self._iso.entries():
                if not is_dir and name.lower().startswith("assets_x.dat"):
                    base = self._iso.base + start * 2048
                    self.archives.append(DaveArchive(path=name, fileobj=self._iso.f, base=base))
        else:
            folder = path if os.path.isdir(path) else os.path.dirname(path)
            for p in sorted(glob.glob(os.path.join(folder, "assets_x.dat*"))):
                self.archives.append(DaveArchive(p))
        if not self.archives:
            raise FileNotFoundError("No assets_x.dat found in " + path)

    def find(self, name):
        for arc in self.archives:
            e = arc.by_name.get(name)
            if e is not None:
                return arc, e
        return None, None

    def read(self, name):
        arc, e = self.find(name)
        if e is None:
            raise KeyError(name)
        return arc.read(e)

    def names(self):
        for arc in self.archives:
            for e in arc.entries:
                yield e.name

    def close(self):
        for arc in self.archives:
            arc.close()
        if self._iso is not None:
            self._iso.close()
