"""Read each shortlisted article's page for its preview image, date and description."""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .collect import clean_text
from .model import Item
from .net import Http

log = logging.getLogger("pline.enrich")


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def _jsonld_date(soup: BeautifulSoup) -> str | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        for obj in data if isinstance(data, list) else [data]:
            if isinstance(obj, dict):
                for key in ("datePublished", "dateCreated"):
                    if isinstance(obj.get(key), str):
                        return obj[key]
    return None


def page_meta(html: str, base_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    image = _meta(soup, "og:image", "og:image:url", "twitter:image", "twitter:image:src")
    date = _meta(soup, "article:published_time", "og:published_time", "date", "pubdate") or _jsonld_date(soup)
    if not date:
        t = soup.find("time", attrs={"datetime": True})
        date = t["datetime"] if t else None
    m = re.match(r"(20\d{2}-\d{2}-\d{2})", date or "")
    title = _meta(soup, "og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else None)
    desc = _meta(soup, "og:description", "description", "twitter:description")
    return {
        "image": urljoin(base_url, image) if image else None,
        "date": m.group(1) if m else None,
        "title": title,
        "description": clean_text(desc or "", 600) if desc else None,
    }


def enrich(http: Http, items: list[Item], limit: int) -> None:
    """Fill in image, date, title and excerpt in place, for at most `limit` items."""
    todo = [it for it in items if not it.image_url or not it.date or len(it.title) < 12 or not it.excerpt][:limit]

    def work(it: Item):
        if not http.allowed(it.url):
            return it, None
        try:
            return it, page_meta(http.get_text(it.url), it.url)
        except Exception as exc:
            log.info("enrich skip %s (%s)", it.url, type(exc).__name__)
            return it, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        for it, meta in pool.map(work, todo):
            if not meta:
                continue
            it.image_url = it.image_url or meta["image"]
            it.date = it.date or meta["date"]
            if meta["title"] and len(it.title) < 12:
                it.title = meta["title"]
            if meta["description"] and not it.excerpt:
                it.excerpt = meta["description"]
    log.info("enriched %d article pages", len(todo))


def image_for(http: Http, url: str) -> str | None:
    """Preview image of one article page, for a chosen story that still has none."""
    try:
        if not http.allowed(url):
            return None
        return page_meta(http.get_text(url), url)["image"]
    except Exception:
        return None
