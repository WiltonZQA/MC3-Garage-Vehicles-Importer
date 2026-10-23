"""Exports a scene (mc3tools.scene) as binary glTF (.glb).

Textures are embedded as PNG. The diffuse texture becomes baseColorTexture
(UV 0). glTF has no lightmap slot, so the lightmap is written as
occlusionTexture on UV 1 (the closest standard equivalent: precomputed shadow)
and also in "extras", for importers that want to multiply it into the color.
"""
import io
import json
import struct

from .textures import png_bytes


def _png_bytes(tex):
    return png_bytes(tex.rgba, tex.width, tex.height)


class _Builder:
    def __init__(self):
        self.bin = io.BytesIO()
        self.views, self.accessors = [], []

    def add_view(self, data, target=None):
        while self.bin.tell() % 4:
            self.bin.write(b"\0")
        view = {"buffer": 0, "byteOffset": self.bin.tell(), "byteLength": len(data)}
        if target:
            view["target"] = target
        self.bin.write(data)
        self.views.append(view)
        return len(self.views) - 1

    def add_accessor(self, fmt, ctype, kind, values, minmax=False, normalized=False):
        flat = [c for v in values for c in (v if isinstance(v, (tuple, list)) else (v,))]
        data = struct.pack("<%d%s" % (len(flat), fmt), *flat)
        target = 34963 if kind == "SCALAR" else 34962
        acc = {"bufferView": self.add_view(data, target), "componentType": ctype,
               "count": len(values), "type": kind}
        if normalized:
            acc["normalized"] = True
        if minmax:
            n = len(values[0])
            acc["min"] = [min(v[i] for v in values) for i in range(n)]
            acc["max"] = [max(v[i] for v in values) for i in range(n)]
        self.accessors.append(acc)
        return len(self.accessors) - 1


def export_glb(scene, path, lightmaps=True):
    b = _Builder()
    gl = {"asset": {"version": "2.0", "generator": "MC3 Importer"}, "scenes": [{"nodes": []}],
          "scene": 0, "nodes": [], "meshes": [], "materials": [], "textures": [], "images": [],
          "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]}

    tex_index = {}
    for name, tex in scene.textures.items():
        img = b.add_view(_png_bytes(tex))
        gl["images"].append({"name": name, "mimeType": "image/png", "bufferView": img})
        gl["textures"].append({"source": len(gl["images"]) - 1, "sampler": 0})
        tex_index[name] = len(gl["textures"]) - 1

    for mat in scene.materials:
        m = {"name": mat.name, "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 0.9},
             "extras": {"mc3_shader": mat.shader, "mc3_diffuse": mat.diffuse, "mc3_lightmap": mat.lightmap}}
        if mat.diffuse in tex_index:
            m["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex_index[mat.diffuse], "texCoord": 0}
            m["alphaMode"] = mat.alpha
            if mat.alpha == "MASK":
                m["alphaCutoff"] = mat.alpha_cutoff
        elif mat.diffuse == "__envmap__":
            # glass/chrome without a texture of its own (environment reflection in game)
            m["pbrMetallicRoughness"].update(baseColorFactor=[0.03, 0.035, 0.04, 0.8], roughnessFactor=0.1)
            m["alphaMode"] = "BLEND"
        if mat.lightmap and lightmaps:
            m["occlusionTexture"] = {"index": tex_index[mat.lightmap], "texCoord": 1}
        gl["materials"].append(m)

    for mesh in scene.meshes:
        attrs = {
            "POSITION": b.add_accessor("f", 5126, "VEC3", mesh.positions, minmax=True),
            "COLOR_0": b.add_accessor("f", 5126, "VEC4", mesh.colors),
            "TEXCOORD_0": b.add_accessor("f", 5126, "VEC2", mesh.uv0),
        }
        if mesh.uv1:
            attrs["TEXCOORD_1"] = b.add_accessor("f", 5126, "VEC2", mesh.uv1)
        prims = []
        for mat_idx, tris in mesh.submeshes:
            flat = [i for t in tris for i in t]
            ind = b.add_accessor("I", 5125, "SCALAR", flat)
            prims.append({"attributes": attrs, "indices": ind, "material": mat_idx, "mode": 4})
        gl["meshes"].append({"name": mesh.name, "primitives": prims})
        gl["nodes"].append({"name": mesh.name, "mesh": len(gl["meshes"]) - 1})
        gl["scenes"][0]["nodes"].append(len(gl["nodes"]) - 1)

    gl["bufferViews"], gl["accessors"] = b.views, b.accessors
    binary = b.bin.getvalue()
    while len(binary) % 4:
        binary += b"\0"
    gl["buffers"] = [{"byteLength": len(binary)}]
    js = json.dumps(gl, separators=(",", ":")).encode()
    while len(js) % 4:
        js += b" "
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(binary)))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(binary), 0x004E4942) + binary)
