from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit, urlunsplit


@dataclass
class Item:
    """One story as collected from a source, before selection."""

    source: str
    title: str
    url: str
    topics: list[str]
    date: str | None = None          # YYYY-MM-DD
    excerpt: str = ""
    lang: str = "en"
    image_url: str | None = None     # remote image found in the feed or page
    weight: float = 1.0
    kind: str = "article"            # article | video | reddit

    @property
    def id(self) -> str:
        return item_id(self.url)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        return d


@dataclass
class Story:
    """One card on the page (possibly several merged Items)."""

    id: str
    topic: str
    source: str
    title: str
    url: str
    summary: str = ""
    date: str | None = None
    title_ja: str | None = None
    image: str | None = None         # local thumbnail path relative to docs/
    also: list[dict] = field(default_factory=list)
    kind: str | None = None          # "video" or "reddit"; None for articles

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", [])}


_TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_)")


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = "&".join(
        p for p in parts.query.split("&") if p and not _TRACKING.match(p.split("=", 1)[0])
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def item_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:12]


def norm_title(title: str) -> str:
    return re.sub(r"[\W_]+", " ", title.lower()).strip()
