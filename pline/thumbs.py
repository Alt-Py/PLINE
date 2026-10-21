"""Turn a remote preview image into a small, clean local thumbnail.

Every image is treated as untrusted:
  * only JPEG, PNG, WebP and GIF are accepted, checked by their first bytes, never by
    file name or by the Content-Type the server claims; SVG (which can carry scripts)
    is always refused;
  * downloads stop at a size cap, and images with absurd pixel counts are refused
    before decoding (decompression bombs);
  * the image is decoded and re-drawn as a brand-new WebP. Only pixels survive:
    metadata, colour profiles, trailing data and anything else hidden in the original
    are discarded. The original bytes are never written to disk.
"""

from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path

from PIL import Image, ImageOps

from .net import Http

log = logging.getLogger("pline.thumbs")

MAGIC = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
)


class Rejected(Exception):
    pass


def sniff(data: bytes) -> str:
    for magic, fmt in MAGIC:
        if data.startswith(magic):
            return fmt
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    raise Rejected("not a JPEG, PNG, WebP or GIF")


def make_thumbnail(data: bytes, width: int = 960, quality: int = 78, max_pixels: int = 40_000_000) -> bytes:
    fmt = sniff(data)
    Image.MAX_IMAGE_PIXELS = max_pixels  # Pillow raises DecompressionBombError above 2x this
    with Image.open(io.BytesIO(data), formats=[fmt]) as im:
        if im.width * im.height > max_pixels:
            raise Rejected(f"{im.width}x{im.height} is too large")
        if im.width < 240 or im.height < 120:
            raise Rejected("too small to be a useful picture")
        if im.format == "JPEG":
            im.draft("RGB", (width * 2, width * 2))
        im.seek(0)  # first frame of animations
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            base = Image.new("RGB", im.size, (255, 255, 255))
            base.paste(im, mask=im.getchannel("A"))
            im = base
        else:
            im = im.convert("RGB")
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "WEBP", quality=quality, method=5)  # no exif/icc passed: stripped
        return out.getvalue()


def thumb_name(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16] + ".webp"


def fetch_thumbnail(http: Http, url: str, out_dir: Path, cfg: dict) -> str | None:
    """Return the thumbnail's file name inside out_dir, or None."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    name = thumb_name(url)
    target = out_dir / name
    if target.exists():
        return name
    try:
        data, _ = http.get_bytes(url, max_bytes=cfg.get("max_bytes", 6_000_000), accept="image/webp,image/jpeg,image/png,image/gif")
        thumb = make_thumbnail(data, cfg.get("width", 960), cfg.get("quality", 78), cfg.get("max_pixels", 40_000_000))
    except Exception as exc:
        log.info("no thumbnail for %s (%s: %s)", url, type(exc).__name__, exc)
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(thumb)
    return name
