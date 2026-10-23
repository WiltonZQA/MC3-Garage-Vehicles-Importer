"""Parser for MC3 (Xbox) .xbck resource packages.

An .xbck is a memory dump: a 0x80-byte header followed by a block whose
pointers are absolute (Xbox virtual addresses). The first dword of the file is
the address the block was loaded at; a pointer v maps to file offset
(v | 0x80000000) - base + 0x80. Physical addresses of D3D resources
(0x0xxxxxxx) use the same conversion.

Vtables differ between game builds (Tokyo, added in the Remix edition, was
written by a different executable), so objects are recognised by structure:

  root (0x80)      {vt, ?, ?, material_list*}
  material list    {vt, items*, u16 n, u16 cap}
  simple material  {vt, 0, texture*}
  shader material  {vt, n, shader_id, shader*, slots*}; slot = {u32 n, item*} every 0x44
  texture ref      {vt, flags, name*, texture*}
  texture          {vt, ?, u16 w, u16 h, block*} ... +0x70 name*; block holds a D3DTexture
  mesh             {vt, u8 stride<<24, n_groups, material_idx*, groups*, 0, VB*, IB*}
  group (24 bytes) {range*, desc[3], 0, u16 ?}; range = {u16 start, u16 count}
  object (prop)    {vt, 1, ?, name*, ...}; the name matches the mesh name
  instance         {vt, next*, prev*, flags, object*, pos[3], rot[3]} (world coordinates,
                   rotation in radians; in practice only yaw, rot[1], is used)
"""
import math
import struct

D3D_TYPE_VERTEXBUFFER = 0
D3D_TYPE_TEXTURE = 4


class Xbck:
    def __init__(self, path_or_bytes):
        if isinstance(path_or_bytes, (bytes, bytearray)):
            self.data = bytes(path_or_bytes)
        else:
            with open(path_or_bytes, "rb") as f:
                self.data = f.read()
        self.base = self.u32(0)

    # --- basic reading --------------------------------------------------
    def u32(self, off):
        return struct.unpack_from("<I", self.data, off)[0]

    def u16(self, off):
        return struct.unpack_from("<H", self.data, off)[0]

    def to_off(self, v):
        if v == 0:
            return None
        o = (v | 0x80000000) - self.base + 0x80
        return o if 0 < o < len(self.data) - 4 else None

    def to_ptr(self, off):
        return (off - 0x80 + self.base) & 0xFFFFFFFF

    def ptr(self, off):
        """Read a pointer at off and return the file offset it points to (or None)."""
        if off is None or off + 4 > len(self.data):
            return None
        return self.to_off(self.u32(off))

    def cstr(self, off, limit=128):
        end = self.data.find(b"\0", off, off + limit)
        if end < 0:
            return None
        s = self.data[off:end].decode("latin-1")
        return s if s and all(32 <= ord(c) < 127 for c in s) else None

    def find_refs(self, target_off):
        """Aligned offsets that hold a pointer to target_off."""
        v = self.to_ptr(target_off)
        out = []
        for pat in {v, v & 0x7FFFFFFF}:
            packed = struct.pack("<I", pat)
            i = self.data.find(packed)
            while i != -1:
                if i % 4 == 0:
                    out.append(i)
                i = self.data.find(packed, i + 1)
        return sorted(out)

    # --- D3D resources --------------------------------------------------
    def d3d_headers(self, d3d_type):
        """Find D3DResource headers {Common, Data, Lock} of the given type."""
        for i in range(0x80, len(self.data) - 20, 4):
            common = self.u32(i)
            if not 0 < (common & 0xFFFF) < 16 or (common >> 16) & 0x7 != d3d_type or common >> 19:
                continue
            d = self.u32(i + 4)
            if 0x00100000 <= d < 0x04000000 and self.to_off(d) is not None and self.u32(i + 8) == 0:
                yield i

    # --- textures -------------------------------------------------------
    def texture_d3d(self, tex_off):
        """For a texture object, return (fmt, w, h, mips, data_off)."""
        hdr = self._texture_header(tex_off)
        if hdr is None:
            raise ValueError("No D3DTexture found for 0x%x" % tex_off)
        fmt = self.u32(hdr + 12)
        return ((fmt >> 8) & 0xFF, 1 << ((fmt >> 20) & 15), 1 << ((fmt >> 24) & 15),
                (fmt >> 16) & 15, self.to_off(self.u32(hdr + 4)))

    def _texture_header(self, tex_off):
        blk = self.ptr(tex_off + 12)
        if blk is None:
            return None
        for d in range(blk, min(blk + 0x40, len(self.data) - 16), 4):
            common = self.u32(d)
            if (common >> 16) & 7 == D3D_TYPE_TEXTURE and 0 < (common & 0xFFFF) < 16 and not common >> 19:
                return d
        return None

    def is_texture(self, off):
        return off is not None and self._texture_header(off) is not None

    def textures(self):
        """All texture objects: the 'block*' of each one holds a D3DTexture."""
        seen = set()
        for hdr in self.d3d_headers(D3D_TYPE_TEXTURE):
            for blk in range(hdr, hdr - 0x40, -4):
                for ref in self.find_refs(blk):
                    tex = ref - 12
                    if tex not in seen and self._texture_header(tex) == hdr:
                        seen.add(tex)
                        yield tex

    def texture_name(self, tex_off):
        """A texture object keeps a pointer to its own name at +0x70."""
        n = self.ptr(tex_off + 0x70)
        return (n is not None and self.cstr(n)) or "tex_%06x" % tex_off

    def resolve_texture(self, off):
        """Follow references until a texture object. Returns (name, tex_off)."""
        name = None
        for _ in range(4):
            if off is None:
                return name, None
            if self.is_texture(off):
                return name or self.texture_name(off), off
            n = self.ptr(off + 8)
            s = self.cstr(n) if n is not None else None
            if s is None:
                return name, None
            name = name or s
            off = self.ptr(off + 12)
        return name, None

    # --- materials ------------------------------------------------------
    def material_list(self):
        lst = self.ptr(0x80 + 12)
        if lst is None:
            return []
        items, count = self.ptr(lst + 4), self.u16(lst + 8)
        if items is None:
            return []
        return [self.ptr(items + 4 * i) for i in range(count)]

    def material(self, off):
        """Return a dict {kind, shader, diffuse, lightmap} with (name, tex_off) pairs."""
        none = (None, None)
        if off is None:
            return {"kind": "missing", "shader": None, "diffuse": none, "lightmap": none}
        if self.u32(off + 4) == 0:
            return {"kind": "simple", "shader": None,
                    "diffuse": self.resolve_texture(self.ptr(off + 8)), "lightmap": none}
        slots_off = self.ptr(off + 16)
        texs = []
        for s in range(2):
            so = None if slots_off is None else slots_off + s * 0x44
            ok = so is not None and 0 < self.u32(so) < 64
            texs.append(self.resolve_texture(self.ptr(so + 4)) if ok else none)
        return {"kind": "shader", "shader": self.u32(off + 8), "diffuse": texs[0], "lightmap": texs[1]}

    # --- meshes ---------------------------------------------------------
    def meshes(self):
        seen = set()
        for vb in self.d3d_headers(D3D_TYPE_VERTEXBUFFER):
            for ref in self.find_refs(vb):
                off = ref - 24
                if off in seen or off < 0x80:
                    continue
                stride, ngroups = self.u32(off + 4) >> 24, self.u32(off + 8)
                if 12 <= stride <= 64 and stride % 4 == 0 and 0 < ngroups < 4096 \
                        and self.ptr(off + 12) and self.ptr(off + 16) and self.ptr(off + 28):
                    seen.add(off)
                    yield self.mesh(off)

    def mesh(self, off):
        ngroups = self.u32(off + 8)
        matidx_off = self.ptr(off + 12)
        groups_off = self.ptr(off + 16)
        vb_hdr = self.ptr(off + 24)
        groups = []
        for g in range(ngroups):
            go = groups_off + g * 24
            rng = self.ptr(go)
            groups.append({
                "material": self.u16(matidx_off + 2 * g),
                "start": self.u16(rng), "count": self.u16(rng + 2),
                "desc": (self.u32(go + 4), self.u32(go + 8), self.u32(go + 12)),
            })
        return {"offset": off, "stride": self.u32(off + 4) >> 24, "groups": groups,
                "vb": self.to_off(self.u32(vb_hdr + 4)), "ib": self.ptr(off + 28),
                "name": self.mesh_name(off), "instances": self.mesh_instances(off)}

    def _mesh_name_span(self, mesh_off):
        end = mesh_off
        while end > 0 and self.data[end - 1] in (0, 0xCD):
            end -= 1
        start = end
        while start > 0 and 32 <= self.data[start - 1] < 127:
            start -= 1
        return start, end

    def mesh_name(self, mesh_off):
        """The name is a string stored right before the mesh object."""
        start, end = self._mesh_name_span(mesh_off)
        return self.data[start:end].decode("latin-1") or "mesh_%x" % mesh_off

    def mesh_instances(self, mesh_off):
        """Instances that place the mesh in the world: [(pos, rot)].

        The chain is name <- object(+0xC) <- instance(+0x10). Empty list if the
        mesh has no instance of its own.
        """
        start, end = self._mesh_name_span(mesh_off)
        if start == end:
            return []
        out = []
        for ref in self.find_refs(start):
            obj = ref - 0xC
            for iref in self.find_refs(obj):
                inst = iref - 0x10
                if inst < 0x80:
                    continue
                vals = struct.unpack_from("<6f", self.data, inst + 0x14)
                if all(abs(v) < 1e-4 for v in vals):
                    continue  # zeroed template object, not a placed instance
                if all(v == v and abs(v) < 1e5 for v in vals) and all(abs(r) <= 2 * math.pi for r in vals[3:]):
                    out.append((vals[:3], vals[3:]))
        return out
