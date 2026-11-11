"""Draw the Factotum app icon (a light dot on the brand teal) as PNGs, with no image library.

Usage: python tools/make_icons.py [OUT_DIR] [--data-uri]   (default OUT_DIR: factotum/web)
--data-uri also prints the 180 px icon as the data URI index.html inlines.
The dot sits well inside the centre 80 % of the square, so the same picture works where a phone
crops icons to a circle or a rounded square ("maskable").
"""
import struct
import sys
import zlib
from pathlib import Path

TEAL = (0x0F, 0x76, 0x6E)
LIGHT = (0xF5, 0xF7, 0xF8)
SS = 4            # supersampling per axis, for a smooth edge


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
            a = hit / (SS * SS)
            rows += bytes(round(TEAL[i] + (LIGHT[i] - TEAL[i]) * a) for i in range(3))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    head = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)      # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")


if __name__ == "__main__":
    import base64
    args = [a for a in sys.argv[1:] if a != "--data-uri"]
    out = Path(args[0]) if args else Path(__file__).resolve().parent.parent / "factotum" / "web"
    out.mkdir(parents=True, exist_ok=True)
    for size in (180, 192, 512):
        (out / f"icon-{size}.png").write_bytes(png(size))
        print(f"icon-{size}.png")
    if "--data-uri" in sys.argv:
        print("data:image/png;base64," + base64.b64encode(png(180)).decode())
