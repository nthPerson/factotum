"""Draw the Factotum icons as PNGs, with no image library: the app icon (a light dot on the brand teal)
and the two long-press shortcut icons (a light plus for New task, a light check mark for Review).

Usage: python tools/make_icons.py [OUT_DIR] [--data-uri]   (default OUT_DIR: factotum/web)
Writes icon-180/192/512.png and icon-new-96.png, icon-review-96.png.
--data-uri also prints the 180 px icon as the data URI index.html inlines.
The dot sits well inside the centre 80 % of the square and each shortcut glyph inside the centre 60 %,
so the same picture works where a phone or a launcher crops icons to a circle or a rounded square ("maskable").
"""
import struct
import sys
import zlib
from pathlib import Path

TEAL = (0x0F, 0x76, 0x6E)
LIGHT = (0xF5, 0xF7, 0xF8)
SS = 4            # supersampling per axis, for a smooth edge

# The shortcut glyphs: thick strokes as segments in fractions of the size, and the stroke width.
GLYPHS = {
    "new": ([((0.27, 0.5), (0.73, 0.5)), ((0.5, 0.27), (0.5, 0.73))], 0.12),           # a plus
    "review": ([((0.27, 0.52), (0.43, 0.68)), ((0.43, 0.68), (0.73, 0.36))], 0.11),     # a check mark
}


def encode(size: int, pixel_rows: bytes) -> bytes:
    """An opaque 8-bit RGB PNG from its scanlines (each one a filter byte, then RGB triples)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    head = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)      # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(bytes(pixel_rows), 9)) + chunk(b"IEND", b"")


def _shade(rows: bytearray, a: float) -> None:
    """Append one pixel: the teal ground mixed toward the light colour by the share a."""
    rows += bytes(round(TEAL[i] + (LIGHT[i] - TEAL[i]) * a) for i in range(3))


def png(size: int, dot: float = 0.26) -> bytes:
    """A size x size opaque PNG: teal ground, a light dot of radius `dot` x size in the middle."""
    c, r2 = size / 2, (dot * size) ** 2
    rows = bytearray()
    for y in range(size):
        rows.append(0)                                   # filter type: none
        for x in range(size):
            hit = 0
            for sy in range(SS):
                dy = y + (sy + 0.5) / SS - c
                for sx in range(SS):
                    dx = x + (sx + 0.5) / SS - c
                    hit += dx * dx + dy * dy <= r2
            _shade(rows, hit / (SS * SS))
    return encode(size, rows)


def _near(px: float, py: float, seg, r2: float) -> bool:
    """Whether the point is within sqrt(r2) of the segment ((ax, ay), (bx, by))."""
    (ax, ay), (bx, by) = seg
    vx, vy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy)))
    dx, dy = px - (ax + t * vx), py - (ay + t * vy)
    return dx * dx + dy * dy <= r2


def shortcut(kind: str, size: int = 96) -> bytes:
    """A size x size opaque PNG for a long-press shortcut: teal ground, the light glyph of `kind`
    ("new" a plus, "review" a check mark) drawn as thick strokes, anti-aliased like png()."""
    segs, width = GLYPHS[kind]
    segs = [((ax * size, ay * size), (bx * size, by * size)) for (ax, ay), (bx, by) in segs]
    r2 = (width * size / 2) ** 2
    rows = bytearray()
    for y in range(size):
        rows.append(0)                                   # filter type: none
        for x in range(size):
            hit = 0
            for sy in range(SS):
                py = y + (sy + 0.5) / SS
                for sx in range(SS):
                    px = x + (sx + 0.5) / SS
                    hit += any(_near(px, py, s, r2) for s in segs)
            _shade(rows, hit / (SS * SS))
    return encode(size, rows)


if __name__ == "__main__":
    import base64
    args = [a for a in sys.argv[1:] if a != "--data-uri"]
    out = Path(args[0]) if args else Path(__file__).resolve().parent.parent / "factotum" / "web"
    out.mkdir(parents=True, exist_ok=True)
    for size in (180, 192, 512):
        (out / f"icon-{size}.png").write_bytes(png(size))
        print(f"icon-{size}.png")
    for kind in GLYPHS:
        (out / f"icon-{kind}-96.png").write_bytes(shortcut(kind))
        print(f"icon-{kind}-96.png")
    if "--data-uri" in sys.argv:
        print("data:image/png;base64," + base64.b64encode(png(180)).decode())
