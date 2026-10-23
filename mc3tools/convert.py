"""Convert MC3 garages and traffic vehicles to .glb (glTF) and, optionally, loose PNGs.

Usage:
    python -I mc3tools/convert.py <file.xbck> <output.glb> [--png <dir>]
    python -I mc3tools/convert.py <ISO or game folder> <output.glb>
           [--city sd|atlanta|detroit|tokyo] [--time midnight|dusk|dawn]
           [--weather clear|cloudy|rainy] [--png <dir>]
    python -I mc3tools/convert.py <ISO or game folder> <output.glb> --vehicle va_taxi_s
    python -I mc3tools/convert.py <ISO or game folder> <dir> --vehicle all
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mc3tools import game, gltf, scene, textures, vehicle  # noqa: E402
from mc3tools.xiso import safe_join  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser(description="Convert MC3 garages and vehicles to glTF (.glb)")
    ap.add_argument("source", help=".xbck, Xbox ISO or folder with assets_x.dat*")
    ap.add_argument("output", help="output .glb file")
    ap.add_argument("--city", choices=game.CITIES, default="sd")
    ap.add_argument("--time", choices=game.TIMES, default="midnight")
    ap.add_argument("--weather", choices=game.WEATHERS, default="clear")
    ap.add_argument("--png", metavar="DIR", help="also write the textures as PNG")
    ap.add_argument("--vehicle", metavar="NAME",
                    help="traffic vehicle (e.g. va_taxi_s) or 'all'; with 'all' the output is a folder")
    args = ap.parse_args(argv[1:])

    if args.vehicle:
        gf = game.GameFiles(args.source)
        try:
            names = vehicle.vehicle_names(gf) if args.vehicle == "all" else [args.vehicle]
            for n in names:
                out = safe_join(args.output, n + ".glb") if args.vehicle == "all" else args.output
                os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
                sc = vehicle.load(gf, n)
                gltf.export_glb(sc, out)
                print("%s: %d meshes, %d textures" % (out, len(sc.meshes), len(sc.textures)))
        finally:
            gf.close()
        return 0

    if args.source.lower().endswith(".xbck"):
        data = args.source
    else:
        gf = game.GameFiles(args.source)
        try:
            data = gf.read(game.garage_entry(args.city, args.time, args.weather))
        finally:
            gf.close()

    sc = scene.load(data)
    gltf.export_glb(sc, args.output)
    if args.png:
        os.makedirs(args.png, exist_ok=True)
        for t in sc.textures.values():
            dest = safe_join(args.png, t.name + ".png")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            textures.write_png(dest, t.rgba, t.width, t.height)
    nv = sum(len(m.positions) for m in sc.meshes)
    nt = sum(len(t) for m in sc.meshes for _, t in m.submeshes)
    print("%s: %d meshes, %d vertices, %d triangles, %d materials, %d textures" % (
        args.output, len(sc.meshes), nv, nt, len(sc.materials), len(sc.textures)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
