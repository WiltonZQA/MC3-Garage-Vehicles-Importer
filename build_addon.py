"""Build dist/io_import_mc3.zip (Blender add-on with mc3tools bundled).

Usage: python -I build_addon.py
"""
import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(ROOT, "blender_addon", "io_import_mc3")
TOOLS = os.path.join(ROOT, "mc3tools")
# Command-line scripts are not needed inside Blender.
SKIP = {"extract.py", "convert.py"}


def main():
    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, "io_import_mc3.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(ADDON)):
            path = os.path.join(ADDON, name)
            if os.path.isfile(path):
                z.write(path, "io_import_mc3/" + name)
        for name in sorted(os.listdir(TOOLS)):
            if name.endswith(".py") and name not in SKIP:
                z.write(os.path.join(TOOLS, name), "io_import_mc3/mc3tools/" + name)
    print(out)


if __name__ == "__main__":
    main()
