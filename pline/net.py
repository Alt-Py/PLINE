"""HTTP helpers: one session, size caps, and robots.txt politeness."""

from __future__ import annotations

import logging
import re
import threading
import urllib.robotparser
from urllib.parse import urlsplit

import requests

log = logging.getLogger("pline.net")


class TooLarge(Exception):
    pass


class Http:
    def __init__(self, cfg: dict):
        self.timeout = cfg.get("timeout", 20)
        self.ua = cfg.get("user_agent", "PlineBot/1.0")
        self.max_page = cfg.get("max_page_bytes", 4_000_000)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.ua,
                "Accept-Language": "en,ja;q=0.8",
            }
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()

    # robots.txt — article pages are only fetched where the site allows it.
    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            if host not in self._robots:
                rp = urllib.robotparser.RobotFileParser()
                try:
                    r = self.session.get(host + "/robots.txt", timeout=self.timeout)
                    if r.status_code >= 400:
                        rp = None  # no robots.txt: allowed
                    else:
                        rp.parse(r.text.splitlines())
                except requests.RequestException:
                    rp = None
                self._robots[host] = rp
            rp = self._robots[host]
        return True if rp is None else rp.can_fetch(self.ua, url)

    def get_bytes(self, url: str, max_bytes: int | None = None, accept: str | None = None) -> tuple[bytes, str]:
        """GET with a hard size cap. Returns (body, content-type)."""
        limit = max_bytes or self.max_page
        headers = {"Accept": accept} if accept else {}
        with self.session.get(url, timeout=self.timeout, stream=True, headers=headers, allow_redirects=True) as r:
            r.raise_for_status()
            declared = r.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > limit:
                raise TooLarge(f"{url}: {declared} bytes")
            buf = bytearray()
            for chunk in r.iter_content(64 * 1024):
                buf.extend(chunk)
                if len(buf) > limit:
                    raise TooLarge(f"{url}: over {limit} bytes")
            return bytes(buf), r.headers.get("Content-Type", "")

    def get_text(self, url: str) -> str:
        body, ctype = self.get_bytes(url)
        return decode(body, ctype)


_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)
_XML_ENC = re.compile(rb"""<\?xml[^>]+encoding=["']([\w-]+)""", re.I)


def decode(body: bytes, ctype: str = "") -> str:
    """Header charset, then <meta>/XML declaration, then a guess."""
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    enc = m.group(1) if m else None
    if not enc:
        head = body[:4096]
        m = _META_CHARSET.search(head) or _XML_ENC.search(head)
        enc = m.group(1).decode() if m else None
    if not enc:
        import charset_normalizer

        guess = charset_normalizer.from_bytes(body).best()
        enc = guess.encoding if guess else "utf-8"
    try:
        return body.decode(enc, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")
