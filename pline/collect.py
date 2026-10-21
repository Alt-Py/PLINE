"""Collect candidate stories from every source in sources.yaml."""

from __future__ import annotations

import html as html_lib
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup

from .model import Item, canonical_url
from .net import Http, decode

log = logging.getLogger("pline.collect")

MAX_PER_SOURCE = 25

_DATE_IN_URL = [
    re.compile(r"(20\d{2})[/-](\d{2})[/-](\d{2})"),
    re.compile(r"/(20\d{2})(\d{2})(\d{2})(?:[-/]|$)"),
]


def date_from_url(url: str) -> str | None:
    for rx in _DATE_IN_URL:
        m = rx.search(url)
        if m:
            y, mo, d = (int(g) for g in m.groups())
            try:
                return datetime(y, mo, d).strftime("%Y-%m-%d")
            except ValueError:
                return None
    return None


def clean_text(html: str, limit: int = 600) -> str:
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def _feed_image(entry) -> str | None:
    for key in ("media_content", "media_thumbnail"):
        for m in entry.get(key, []) or []:
            url = m.get("url")
            if url and not url.lower().split("?")[0].endswith(".svg"):
                return url
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image/"):
            return link.get("href")
    # First <img> in the content or summary.
    html = ""
    if entry.get("content"):
        html = entry["content"][0].get("value", "")
    html = html or entry.get("summary", "")
    m = re.search(r"<img[^>]+src=[\"']([^\"']+)", html or "")
    if m and not m.group(1).lower().endswith(".svg"):
        return m.group(1)
    return None


def _entry_date(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return time.strftime("%Y-%m-%d", t)
    return None


def parse_feed(body: bytes, src: dict) -> list[Item]:
    feed = feedparser.parse(body)
    title_filter = re.compile(src["title_filter"]) if src.get("title_filter") else None
    items = []
    for e in feed.entries[:MAX_PER_SOURCE * 2]:
        title = clean_text(e.get("title", ""), 300)
        link = e.get("link")
        if not title or not link:
            continue
        if title_filter and not title_filter.search(title):
            continue
        excerpt = clean_text((e.get("summary") or "") or (e.get("content") or [{}])[0].get("value", ""))
        items.append(
            Item(
                source=src["name"],
                title=title,
                url=link,
                topics=list(src["topics"]),
                date=_entry_date(e) or date_from_url(link),
                excerpt=excerpt,
                lang=src.get("lang", "en"),
                image_url=_feed_image(e),
                weight=float(src.get("weight", 1)),
                kind=src.get("kind", "article"),
            )
        )
        if len(items) >= MAX_PER_SOURCE:
            break
    return items


# ── Reddit (official API, app-only OAuth; RSS fallback) ───

_reddit_token: dict = {}
_reddit_lock = threading.Lock()


def _reddit_auth(http: Http) -> str | None:
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        return None
    with _reddit_lock:
        if _reddit_token.get("exp", 0) > time.time() + 60:
            return _reddit_token["value"]
        r = http.session.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(cid, secret),
            timeout=http.timeout,
        )
        r.raise_for_status()
        data = r.json()
        _reddit_token.update(value=data["access_token"], exp=time.time() + int(data.get("expires_in", 3600)))
        return _reddit_token["value"]


def parse_reddit_listing(data: dict, src: dict) -> list[Item]:
    items = []
    for child in data.get("data", {}).get("children", []):
        p = child.get("data", {})
        if p.get("stickied") or p.get("over_18") or p.get("score", 0) < src.get("min_score", 20):
            continue
        image = None
        try:
            image = html_lib.unescape(p["preview"]["images"][0]["source"]["url"])
        except (KeyError, IndexError, TypeError):
            thumb = p.get("thumbnail", "")
            image = thumb if thumb.startswith("http") else None
        stats = f"{p.get('score', 0)} upvotes, {p.get('num_comments', 0)} comments on r/{p.get('subreddit', '')}."
        text = clean_text(p.get("selftext_html") or html_lib.escape(p.get("selftext", "")), 380)
        items.append(
            Item(
                source=f"r/{p.get('subreddit', src.get('subreddit'))}",
                title=html_lib.unescape(p.get("title", "")),
                url="https://www.reddit.com" + p.get("permalink", ""),
                topics=list(src["topics"]),
                date=datetime.fromtimestamp(p.get("created_utc", time.time()), timezone.utc).strftime("%Y-%m-%d"),
                excerpt=(text + " " if text else "") + stats,
                image_url=image,
                weight=float(src.get("weight", 0.6)),
                kind="reddit",
            )
        )
    return items


def reddit(http: Http, src: dict) -> list[Item]:
    sub = src["subreddit"]
    token = _reddit_auth(http)
    if token:
        r = http.session.get(
            f"https://oauth.reddit.com/r/{sub}/top",
            params={"t": src.get("period", "day"), "limit": 25, "raw_json": 1},
            headers={"Authorization": f"Bearer {token}"},
            timeout=http.timeout,
        )
        r.raise_for_status()
        return parse_reddit_listing(r.json(), src)
    # Without API credentials, try the public feed (Reddit often blocks cloud runners).
    body, _ = http.get_bytes(f"https://www.reddit.com/r/{sub}/top/.rss?t={src.get('period', 'day')}")
    return parse_feed(body, {**src, "name": f"r/{sub}", "kind": "reddit"})


# ── YouTube Data API search (needs YOUTUBE_API_KEY) ──────

def youtube_search(http: Http, src: dict) -> list[Item]:
    key = os.environ["YOUTUBE_API_KEY"]
    after = (datetime.now(timezone.utc) - timedelta(days=src.get("days", 7))).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "part": "snippet", "type": "video", "q": src["query"], "order": src.get("order", "relevance"),
        "publishedAfter": after, "maxResults": src.get("max", 10), "videoDuration": "medium", "key": key,
    }
    if src.get("lang"):
        params["relevanceLanguage"] = src["lang"]
    r = http.session.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=http.timeout)
    r.raise_for_status()
    results = r.json().get("items", [])
    ids = [x["id"]["videoId"] for x in results if x.get("id", {}).get("videoId")]
    views: dict[str, int] = {}
    if ids:  # 1 quota unit for view counts, so the shortlist can tell hits from noise
        s = http.session.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "statistics", "id": ",".join(ids), "key": key},
            timeout=http.timeout,
        )
        if s.ok:
            views = {v["id"]: int(v.get("statistics", {}).get("viewCount", 0)) for v in s.json().get("items", [])}
    items = []
    for x in results:
        vid = x.get("id", {}).get("videoId")
        sn = x.get("snippet", {})
        if not vid:
            continue
        thumbs = sn.get("thumbnails", {})
        image = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url")
        items.append(
            Item(
                source=html_lib.unescape(sn.get("channelTitle", "YouTube")),
                title=html_lib.unescape(sn.get("title", "")),
                url=f"https://www.youtube.com/watch?v={vid}",
                topics=list(src["topics"]),
                date=(sn.get("publishedAt") or "")[:10] or None,
                excerpt=html_lib.unescape(sn.get("description", ""))[:380] + (f" ({views[vid]:,} views)" if vid in views else ""),
                lang=src.get("lang", "en") if src.get("lang") == "ja" else "en",
                image_url=image,
                weight=float(src.get("weight", 0.8)),
                kind="video",
            )
        )
    return items


def parse_listing(html: str, src: dict) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    pattern = re.compile(src["link_pattern"])
    exclude = re.compile(src["exclude_pattern"]) if src.get("exclude_pattern") else None
    found: dict[str, Item] = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(src["url"], a["href"])
        if not pattern.search(url) or (exclude and exclude.search(url)):
            continue
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        key = canonical_url(url)
        # Several links often point to one article (image + headline); keep the longest text.
        prev = found.get(key)
        if prev and len(prev.title) >= len(title):
            continue
        img = a.find("img")
        image_url = urljoin(src["url"], img["src"]) if img and img.get("src") else (prev.image_url if prev else None)
        found[key] = Item(
            source=src["name"],
            title=title,
            url=url,
            topics=list(src["topics"]),
            date=date_from_url(url),
            lang=src.get("lang", "en"),
            image_url=image_url,
            weight=float(src.get("weight", 1)),
        )
        if len(found) >= MAX_PER_SOURCE:
            break
    # Listings sometimes put a date or category in the link text; very short texts get
    # their real title from the article page during enrichment.
    return list(found.values())


SKIPPED = "skipped:"


def collect_source(http: Http, src: dict) -> tuple[list[Item], str | None]:
    try:
        if src["type"] == "rss":
            body, _ = http.get_bytes(src["url"], accept="application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5")
            items = parse_feed(body, src)
        elif src["type"] == "youtube_channel":
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={src['channel_id']}"
            body, _ = http.get_bytes(url)
            items = [it for it in parse_feed(body, {**src, "kind": "video"}) if "/shorts/" not in it.url]
        elif src["type"] == "youtube_search":
            if not os.environ.get("YOUTUBE_API_KEY"):
                return [], f"{SKIPPED} add the YOUTUBE_API_KEY secret to enable"
            items = youtube_search(http, src)
        elif src["type"] == "reddit":
            items = reddit(http, src)
        elif src["type"] == "html_list":
            body, ctype = http.get_bytes(src["url"])
            items = parse_listing(decode(body, ctype), src)
        else:
            return [], f"unknown type {src['type']}"
        if not items:
            return [], "no stories found"
        return items, None
    except Exception as exc:  # one broken source never stops the edition
        return [], f"{type(exc).__name__}: {exc}"[:200]


def collect_all(http: Http, sources: list[dict]) -> tuple[list[Item], dict[str, str | None]]:
    report: dict[str, str | None] = {}
    items: list[Item] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for src, (got, err) in zip(sources, pool.map(lambda s: collect_source(http, s), sources)):
            report[src["name"]] = err
            if err and err.startswith(SKIPPED):
                log.info("source %-28s %s", src["name"], err)
            elif err:
                log.warning("source %-28s FAILED %s", src["name"], err)
            else:
                log.info("source %-28s %d stories", src["name"], len(got))
            items.extend(got)
    return items, report


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
