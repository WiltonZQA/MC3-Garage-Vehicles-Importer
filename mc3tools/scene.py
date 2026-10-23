"""Turns an MC3 .xbck into a neutral scene (meshes, materials and textures).

Scene conventions (same as the game / Direct3D):
  - Y up, units in meters;
  - UV origin at the top-left corner;
  - vertex color RGBA in 0..1.
"""
import math
import struct

from . import textures
from .xbck import Xbck


class Texture:
    def __init__(self, name, width, height, rgba):
        self.name, self.width, self.height, self.rgba = name, width, height, rgba


class Material:
    """alpha: what the diffuse texture's alpha means in the game shader.

    "OPAQUE" (ignored: reflection mask etc.), "MASK" (cutout) or "BLEND".
    """

    def __init__(self, name, diffuse, lightmap, shader, alpha="OPAQUE", alpha_cutoff=0.5):
        self.name, self.diffuse, self.lightmap, self.shader = name, diffuse, lightmap, shader
        self.alpha, self.alpha_cutoff = alpha, alpha_cutoff


# Garages: alpha is a cutout only in the scenery shaders; in the car and chrome
# shaders (6, 32-37) it is a reflection mask. Shader 5 is an additive glow.
GARAGE_ALPHA = {None: "MASK", 30: "MASK", 31: "MASK", 5: "BLEND"}


class Mesh:
    def __init__(self, name):
        self.name = name
        self.positions = []
        self.colors = []
        self.uv0 = []
        self.uv1 = None
        self.submeshes = []  # [(material_index, [(a, b, c), ...])]


class Scene:
    def __init__(self):
        self.meshes = []
        self.materials = []
        self.textures = {}


CARPAINT_SHADER = 32  # garage_car_carpaint.fx: color comes from the 2nd texture (*spot)


def rotation(rx, ry, rz):
    """3x3 matrix (rows) for an Euler rotation in radians, order Y·X·Z."""
    def rx_(a):
        c, s = math.cos(a), math.sin(a)
        return ((1, 0, 0), (0, c, -s), (0, s, c))

    def ry_(a):
        c, s = math.cos(a), math.sin(a)
        return ((c, 0, s), (0, 1, 0), (-s, 0, c))

    def rz_(a):
        c, s = math.cos(a), math.sin(a)
        return ((c, -s, 0), (s, c, 0), (0, 0, 1))

    def mul(a, b):
        return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))

    return mul(mul(ry_(ry), rx_(rx)), rz_(rz))


def _apply(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


def _transposed(m):
    return tuple(tuple(m[j][i] for j in range(3)) for i in range(3))


def strip_to_triangles(idx):
    tris = []
    for i in range(len(idx) - 2):
        a, b, c = idx[i], idx[i + 1], idx[i + 2]
        if a == b or b == c or a == c:
            continue
        tris.append((a, b, c) if i % 2 == 0 else (a, c, b))
    return tris


def load(path_or_bytes, decode_textures=True):
    x = Xbck(path_or_bytes)
    scene = Scene()

    def texture(ref):
        name, off = ref
        if off is None:
            return None
        if name not in scene.textures:
            fmt, w, h, _mips, d = x.texture_d3d(off)
            rgba = textures.decode(x.data, d, fmt, w, h) if decode_textures else None
            scene.textures[name] = Texture(name, w, h, rgba)
        return name

    for i, moff in enumerate(x.material_list()):
        info = x.material(moff)
        diff, lm = texture(info["diffuse"]), texture(info["lightmap"])
        if info["shader"] == CARPAINT_SHADER and lm:
            # Car paint: the shader uses the 2nd texture (a *spot gradient) as the color.
            diff, lm = lm, None
        label = "%02d_%s" % (i, diff or "untextured")
        if lm:
            label += "__" + lm
        scene.materials.append(Material(label, diff, lm, info["shader"],
                                        GARAGE_ALPHA.get(info["shader"], "OPAQUE")))

    raw = list(x.meshes())
    # Pieces are placed in the world by instances (position + rotation).
    # Everything is expressed in the frame of the largest mesh's instance (the room).
    ref = None
    for m in sorted(raw, key=lambda m: -max(g["start"] + g["count"] for g in m["groups"])):
        if m["instances"]:
            ref = m["instances"][0]
            break
    if ref is not None:
        ref_t, ref_rt = ref[0], _transposed(rotation(*ref[1]))

    for m in raw:
        base = _read_mesh(x, m)
        local = base.positions
        placements = m["instances"] if ref is not None and m["instances"] else [None]
        for k, inst in enumerate(placements):
            mesh = base if k == 0 else _copy_mesh(base, "%s.%03d" % (base.name, k))
            if inst is not None:
                rot = rotation(*inst[1])
                off = tuple(inst[0][i] - ref_t[i] for i in range(3))
                mesh.positions = [_apply(ref_rt, tuple(a + b for a, b in zip(_apply(rot, p), off)))
                                  for p in local]
            scene.meshes.append(mesh)
    return scene


def _copy_mesh(src, name):
    m = Mesh(name)
    m.positions, m.colors, m.uv0, m.uv1, m.submeshes = src.positions, src.colors, src.uv0, src.uv1, src.submeshes
    return m


def _read_mesh(x, m):
    stride = m["stride"]
    mesh = Mesh(m["name"])
    groups = m["groups"]
    total = max(g["start"] + g["count"] for g in groups)
    idx = struct.unpack_from("<%dH" % total, x.data, m["ib"])
    nverts = max(idx) + 1
    for v in range(nverts):
        o = m["vb"] + v * stride
        px, py, pz = struct.unpack_from("<3f", x.data, o)
        b, g, r, a = x.data[o + 12:o + 16]
        mesh.positions.append((px, py, pz))
        mesh.colors.append((r / 255.0, g / 255.0, b / 255.0, a / 255.0))
        mesh.uv0.append(struct.unpack_from("<2f", x.data, o + 16))
        if stride >= 32:
            if mesh.uv1 is None:
                mesh.uv1 = []
            mesh.uv1.append(struct.unpack_from("<2f", x.data, o + 24))
    for g in groups:
        tris = strip_to_triangles(idx[g["start"]:g["start"] + g["count"]])
        if tris:
            mesh.submeshes.append((g["material"], tris))
    return mesh
