"""Collect candidate stories from every source in sources.yaml."""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
            )
        )
        if len(items) >= MAX_PER_SOURCE:
            break
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


def collect_source(http: Http, src: dict) -> tuple[list[Item], str | None]:
    try:
        if src["type"] == "rss":
            body, _ = http.get_bytes(src["url"], accept="application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5")
            items = parse_feed(body, src)
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
            if err:
                log.warning("source %-28s FAILED %s", src["name"], err)
            else:
                log.info("source %-28s %d stories", src["name"], len(got))
            items.extend(got)
    return items, report


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
