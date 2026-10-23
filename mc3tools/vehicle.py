"""MC3 vehicles (vehicle/<name>/ folder inside the Dave archives).

Files of a vehicle, all plain text except the textures:
  <name>.type   shaders (with textures) and which .mesh goes on which bone
  <name>.skel   bone hierarchy with offset/euler relative to the parent
  *.mesh        Pos/Nrm/Cpv/Tex0 + Adj (corners = P/N/C0/T0 indices) + Mtl "#k"
                with TRISTRIP/TRISTRIP2 primitives indexing Adj
  texture_x/*.tex  14-byte header {u16 w, u16 h, u16 format, u16 mips, ...}
                   followed by D3D data (0x16 = DXT1, 0x1A = DXT5)
"""
import re
import struct

from . import textures
from .scene import Material, Mesh, Scene, Texture, _apply, rotation

TEX_FORMATS = {0x16: textures.FMT_DXT1, 0x1A: textures.FMT_DXT5}
ENVMAP = "__envmap__"
# Alpha usage according to shaderlibx/cars/*.fx. In the other shaders
# (ambient_body, train, train_inside...) alpha is not transparency: the train
# texture, for example, has almost all-zero alpha and is opaque in game.
VEHICLE_ALPHA = {"train_people": "MASK", "ambient_wheel": "MASK",
                 "ambient_shadow": "BLEND", "train_shadow": "BLEND"}


# --- text formats -----------------------------------------------------------
def parse_type(text):
    shaders = []
    m = re.search(r"Shaders\s+\d+\s*\{(.*?)\}", text, re.S)
    if m:
        for line in m.group(1).strip().splitlines():
            parts = line.split()
            if parts and parts[0].endswith(".shadert"):
                n = int(parts[1])
                shaders.append((parts[0][:-len(".shadert")], parts[2:2 + n]))
    parts = []
    m = re.search(r"high\s+(\d+)\s+(.*?)\n", text)
    if m:
        toks = m.group(2).split()
        for i in range(int(m.group(1))):
            parts.append((toks[2 * i].lower(), int(toks[2 * i + 1])))
    return shaders, parts


def parse_skel(text):
    """Return {index: (name, world_offset, world_rotation)} accumulating the hierarchy."""
    bones = {}
    stack = []  # [(world_offset, world_rot)]
    pending = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"bone(\d+)\s+(\S+)\s*\{", s)
        if m:
            pending = [int(m.group(1)), m.group(2), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)]
            stack.append(None)
            continue
        if pending is not None and s.startswith("offset"):
            pending[2] = tuple(map(float, s.split()[1:4]))
        elif pending is not None and s.startswith("euler"):
            pending[3] = tuple(map(float, s.split()[1:4]))
            idx, name, off, eul = pending
            parent = next((p for p in reversed(stack[:-1]) if p is not None), ((0.0, 0.0, 0.0), rotation(0, 0, 0)))
            prot = parent[1]
            woff = tuple(a + b for a, b in zip(parent[0], _apply(prot, off)))
            wrot = _mul(prot, rotation(*eul))
            stack[-1] = (woff, wrot)
            bones[idx] = (name, woff, wrot)
            pending = None
        elif s == "}" and stack:
            stack.pop()
    return bones


def _mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _block(text, name):
    """Numbers of the block 'name N { ... }' (first one only)."""
    m = re.search(r"\b%s\s+(\d+)\s*\{" % name, text)
    if not m:
        return 0, []
    end = text.index("}", m.end())
    return int(m.group(1)), text[m.end():end].split()


def parse_mesh(text):
    n, vals = _block(text, "Pos")
    pos = [tuple(map(float, vals[i * 3:i * 3 + 3])) for i in range(n)]
    n, vals = _block(text, "Cpv")
    cpv = [tuple(map(float, vals[i * 4:i * 4 + 4])) for i in range(n)]
    n, vals = _block(text, "Tex0")
    tex0 = [tuple(map(float, vals[i * 2:i * 2 + 2])) for i in range(n)]
    n, vals = _block(text, "Adj")
    adj = []
    cur = None
    for k, v in zip(vals[0::2], map(int, vals[1::2])):
        if k == "P":  # every corner starts with P
            if cur is not None:
                adj.append(cur)
            cur = {}
        cur[k] = v
    if cur is not None:
        adj.append(cur)
    adj = [(c.get("P", 0), c.get("C0", -1), c.get("T0", -1)) for c in adj]
    mats = []
    mtl = text.find("Mtl")
    for block in re.split(r'Name\s+"', text[mtl:])[1:]:
        name = block[:block.index('"')]
        strips = []
        for typ, cnt, body in re.findall(r"Type\s+(\w+).*?Idx\s+(\d+)\s*\{([^}]*)\}", block, re.S):
            strips.append((typ, list(map(int, body.split()))))
        mats.append((name, strips))
    return pos, cpv, tex0, adj, mats


def decode_tex(data):
    w, h, fmt = struct.unpack_from("<HHH", data)
    xfmt = TEX_FORMATS.get(fmt)
    if xfmt is None:
        raise NotImplementedError(".tex format 0x%02x" % fmt)
    return w, h, textures.decode(data, 14, xfmt, w, h)


# --- assembly ---------------------------------------------------------------
def strip_tris(idx, flipped):
    tris = []
    for i in range(len(idx) - 2):
        a, b, c = idx[i], idx[i + 1], idx[i + 2]
        if a == b or b == c or a == c:
            continue
        odd = (i % 2 == 1) != flipped
        tris.append((a, c, b) if odd else (a, b, c))
    return tris


CITY_SUFFIX = {"a": "Atlanta", "d": "Detroit", "s": "San Diego", "t": "Tokyo", "sh": "all cities",
               "sd": "San Diego", "detroit": "Detroit", "tokyo": "Tokyo"}
NICE_NAMES = {
    "18wheeler": "18-Wheeler", "18wheelertanker": "18-Wheeler Tanker", "cargotruck": "Cargo Truck",
    "cementtruck": "Cement Truck", "citybus": "City Bus", "constructiontruck": "Construction Truck",
    "cop": "Police Car", "cranetruck": "Crane Truck", "deliverytruck": "Delivery Truck",
    "firetruck": "Fire Truck", "garbagetruck": "Garbage Truck", "gettocar": "Ghetto Car",
    "odessey": "Odyssey", "surftruck": "Surf Truck", "utilityvan": "Utility Van",
}


def vehicle_label(name):
    """'va_taxi_s' -> 'Taxi (San Diego)', 'va_tram_sd' -> 'Tram (San Diego)'."""
    base, _, suffix = name[3:].rpartition("_")
    if suffix not in CITY_SUFFIX:
        base, suffix = name[3:], ""
    label = NICE_NAMES.get(base, base.replace("_", " ").title())
    return "%s (%s)" % (label, CITY_SUFFIX[suffix]) if suffix else label


def vehicle_names(gf):
    names = set()
    for n in gf.names():
        m = re.match(r"vehicle/(va_[^/]+)/\1\.type$", n)
        if m:
            names.add(m.group(1))
    return sorted(names)


def load(gf, name, include_shadow=False):
    """Build the scene for a vehicle (e.g. 'va_taxi_s') from GameFiles."""
    base = "vehicle/%s/" % name
    shaders, parts = parse_type(gf.read(base + name + ".type").decode("latin-1"))
    bones = parse_skel(gf.read(base + name + ".skel").decode("latin-1"))
    sc = Scene()

    def texture(fname):
        if fname == ENVMAP:
            return ENVMAP
        key = fname[:-4] if fname.lower().endswith(".tex") else fname
        if key not in sc.textures:
            for path in (base + "texture_x/" + fname, "vehicle/shared/texture_x/" + fname):
                arc, e = gf.find(path)
                if e is not None:
                    w, h, rgba = decode_tex(arc.read(e))
                    sc.textures[key] = Texture(key, w, h, rgba)
                    break
            else:
                return None
        return key

    for i, (sh, texs) in enumerate(shaders):
        diff = texture(texs[0]) if texs else None
        # AlphaFunc Greater with AlphaRef 0..5: only cut what is practically transparent
        sc.materials.append(Material("%02d_%s_%s" % (i, sh, diff or "untextured"), diff, None, sh,
                                     VEHICLE_ALPHA.get(sh, "OPAQUE"), alpha_cutoff=0.02))

    for fname, bone in parts:
        if "shadow" in fname and not include_shadow:
            continue
        arc, e = gf.find(base + fname)
        if e is None:
            continue
        pos, cpv, tex0, adj, mats = parse_mesh(arc.read(e).decode("latin-1"))
        _bname, boff, brot = bones.get(bone, ("", (0.0, 0.0, 0.0), rotation(0, 0, 0)))
        mesh = Mesh(fname[:-5])
        for p, c, t in adj:
            mesh.positions.append(tuple(a + b for a, b in zip(boff, _apply(brot, pos[p]))))
            r, g, b = cpv[c][:3] if 0 <= c < len(cpv) else (1.0, 1.0, 1.0)
            mesh.colors.append((r, g, b, 1.0))
            mesh.uv0.append(tex0[t] if 0 <= t < len(tex0) else (0.0, 0.0))
        for mname, strips in mats:
            mi = int(mname.lstrip("#")) if mname.lstrip("#").isdigit() else 0
            tris = []
            for typ, idx in strips:
                tris += strip_tris(idx, typ == "TRISTRIP2")
            if tris and mi < len(sc.materials):
                mesh.submeshes.append((mi, tris))
        only_shadow = all("shadow" in sc.materials[mi].shader for mi, _ in mesh.submeshes)
        if mesh.submeshes and (include_shadow or not only_shadow):
            sc.meshes.append(mesh)
    return sc
