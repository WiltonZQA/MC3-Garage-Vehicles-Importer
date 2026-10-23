"""Decoding of original Xbox D3D textures to RGBA, and PNG writing.

Uses only the standard library so it also runs inside Blender.
"""
import struct
import zlib

# Original Xbox D3D format codes (byte 1 of the Format field).
FMT_A8R8G8B8 = 0x06
FMT_X8R8G8B8 = 0x07
FMT_R5G6B5 = 0x05
FMT_A4R4G4B4 = 0x04
FMT_A1R5G5B5 = 0x02
FMT_L8 = 0x00
FMT_DXT1 = 0x0C
FMT_DXT3 = 0x0E
FMT_DXT5 = 0x0F
FMT_LIN_A8R8G8B8 = 0x12
FMT_LIN_X8R8G8B8 = 0x1E

BPP = {FMT_A8R8G8B8: 4, FMT_X8R8G8B8: 4, FMT_R5G6B5: 2, FMT_A4R4G4B4: 2,
       FMT_A1R5G5B5: 2, FMT_L8: 1, FMT_LIN_A8R8G8B8: 4, FMT_LIN_X8R8G8B8: 4}
SWIZZLED = {FMT_A8R8G8B8, FMT_X8R8G8B8, FMT_R5G6B5, FMT_A4R4G4B4, FMT_A1R5G5B5, FMT_L8}


def data_size(fmt, w, h):
    if fmt == FMT_DXT1:
        return max(1, w // 4) * max(1, h // 4) * 8
    if fmt in (FMT_DXT3, FMT_DXT5):
        return max(1, w // 4) * max(1, h // 4) * 16
    return w * h * BPP[fmt]


def _rgb565(c):
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _color_block(data, o, out, w, x0, y0, h, alpha_from_c1=True):
    c0, c1, bits = struct.unpack_from("<HHI", data, o)
    p0, p1 = _rgb565(c0), _rgb565(c1)
    if c0 > c1 or not alpha_from_c1:
        pal = (p0 + (255,), p1 + (255,),
               tuple((2 * a + b) // 3 for a, b in zip(p0, p1)) + (255,),
               tuple((a + 2 * b) // 3 for a, b in zip(p0, p1)) + (255,))
    else:
        pal = (p0 + (255,), p1 + (255,),
               tuple((a + b) // 2 for a, b in zip(p0, p1)) + (255,), (0, 0, 0, 0))
    for py in range(4):
        y = y0 + py
        if y >= h:
            break
        row = y * w
        for px in range(4):
            x = x0 + px
            if x < w:
                c = pal[(bits >> (2 * (py * 4 + px))) & 3]
                i = (row + x) * 4
                out[i:i + 4] = bytes(c)


def _dxt5_alpha(data, o):
    a0, a1 = data[o], data[o + 1]
    bits = int.from_bytes(data[o + 2:o + 8], "little")
    if a0 > a1:
        pal = [a0, a1] + [((6 - i) * a0 + (i + 1) * a1) // 7 for i in range(6)]
    else:
        pal = [a0, a1] + [((4 - i) * a0 + (i + 1) * a1) // 5 for i in range(4)] + [0, 255]
    return [pal[(bits >> (3 * i)) & 7] for i in range(16)]


def decode_dxt(data, off, fmt, w, h):
    out = bytearray(w * h * 4)
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    block = 8 if fmt == FMT_DXT1 else 16
    o = off
    for by in range(bh):
        for bx in range(bw):
            if fmt == FMT_DXT1:
                _color_block(data, o, out, w, bx * 4, by * 4, h)
            else:
                _color_block(data, o + 8, out, w, bx * 4, by * 4, h, alpha_from_c1=False)
                if fmt == FMT_DXT5:
                    alphas = _dxt5_alpha(data, o)
                else:
                    a = int.from_bytes(data[o:o + 8], "little")
                    alphas = [((a >> (4 * i)) & 15) * 17 for i in range(16)]
                for i, av in enumerate(alphas):
                    x, y = bx * 4 + (i & 3), by * 4 + (i >> 2)
                    if x < w and y < h:
                        out[(y * w + x) * 4 + 3] = av
            o += block
    return bytes(out)


def _morton(x, y, w, h):
    """Swizzled (Z-order) address used by the Xbox for uncompressed textures."""
    idx, bit, sx, sy = 0, 0, 1, 1
    while sx < w or sy < h:
        if sx < w:
            idx |= ((x >> (sx.bit_length() - 1)) & 1) << bit
            bit += 1
            sx <<= 1
        if sy < h:
            idx |= ((y >> (sy.bit_length() - 1)) & 1) << bit
            bit += 1
            sy <<= 1
    return idx


def decode_uncompressed(data, off, fmt, w, h):
    bpp = BPP[fmt]
    out = bytearray(w * h * 4)
    swz = fmt in SWIZZLED
    for y in range(h):
        for x in range(w):
            src = off + (_morton(x, y, w, h) if swz else y * w + x) * bpp
            if bpp == 4:
                b, g, r, a = data[src:src + 4]
                if fmt in (FMT_X8R8G8B8, FMT_LIN_X8R8G8B8):
                    a = 255
            elif bpp == 2:
                c = data[src] | (data[src + 1] << 8)
                if fmt == FMT_R5G6B5:
                    r, g, b = _rgb565(c)
                    a = 255
                elif fmt == FMT_A4R4G4B4:
                    a, r, g, b = ((c >> 12) & 15) * 17, ((c >> 8) & 15) * 17, ((c >> 4) & 15) * 17, (c & 15) * 17
                else:
                    a = 255 if c >> 15 else 0
                    r, g, b = (((c >> 10) & 31) * 255) // 31, (((c >> 5) & 31) * 255) // 31, ((c & 31) * 255) // 31
            else:
                r = g = b = data[src]
                a = 255
            i = (y * w + x) * 4
            out[i:i + 4] = bytes((r, g, b, a))
    return bytes(out)


def decode(data, off, fmt, w, h):
    """Decode mip level 0. Returns RGBA bytes (top row first)."""
    if fmt in (FMT_DXT1, FMT_DXT3, FMT_DXT5):
        return decode_dxt(data, off, fmt, w, h)
    if fmt in BPP:
        return decode_uncompressed(data, off, fmt, w, h)
    raise NotImplementedError("Unsupported Xbox texture format 0x%02x" % fmt)


def png_bytes(rgba, w, h):
    raw = b"".join(b"\0" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def write_png(path, rgba, w, h):
    with open(path, "wb") as f:
        f.write(png_bytes(rgba, w, h))
