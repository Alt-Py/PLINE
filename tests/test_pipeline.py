import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from PIL import Image

from pline.collect import date_from_url, parse_feed, parse_listing
from pline.enrich import page_meta
from pline.model import Item, item_id
from pline.select import _parse_json, plain, shortlist
from pline.thumbs import Rejected, make_thumbnail, sniff

FIX = Path(__file__).parent / "fixtures"


def png_bytes(w=800, h=500, mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, (w, h), (200, 30, 30) if mode == "RGB" else (200, 30, 30, 128)).save(buf, "PNG")
    return buf.getvalue()


class CollectTests(unittest.TestCase):
    def test_feed(self):
        src = {"name": "b&a", "topics": ["vfx"], "weight": 1.5}
        items = parse_feed((FIX / "feed.xml").read_bytes(), src)
        self.assertEqual(len(items), 2)
        a = items[0]
        self.assertEqual(a.title, "Digital Domain’s breakdown for Disclosure Day")
        self.assertEqual(a.date, "2026-10-19")
        self.assertEqual(a.image_url, "https://example.com/a.jpg")
        self.assertIn("shows the train scene", a.excerpt)
        self.assertEqual(items[1].image_url, "https://example.com/b.png")

    def test_title_filter(self):
        src = {"name": "x", "topics": ["vfx"], "title_filter": "(?i)disclosure"}
        self.assertEqual(len(parse_feed((FIX / "feed.xml").read_bytes(), src)), 1)

    def test_listing(self):
        src = {"name": "Sailor", "url": "https://sailor.co.jp/news/", "link_pattern": r"sailor\.co\.jp/news/\d{8}(-\d+)?/?$",
               "topics": ["stationery"], "lang": "ja"}
        items = parse_listing((FIX / "listing.html").read_text(encoding="utf-8"), src)
        self.assertEqual([i.url for i in items], ["https://sailor.co.jp/news/20261014/", "https://sailor.co.jp/news/20260831/"])
        self.assertEqual(items[0].title, "長刀研ぎエボナイト万年筆 焔龍")
        self.assertEqual(items[0].date, "2026-10-14")
        self.assertEqual(items[0].image_url, "https://sailor.co.jp/img/honryu.jpg")
        self.assertEqual(items[0].lang, "ja")

    def test_dates_in_urls(self):
        self.assertEqual(date_from_url("https://x.com/2026/10/20/post/"), "2026-10-20")
        self.assertEqual(date_from_url("https://sailor.co.jp/news/20261014/"), "2026-10-14")
        self.assertIsNone(date_from_url("https://x.com/news/hello"))

    def test_ids_ignore_tracking(self):
        self.assertEqual(item_id("https://a.com/x/?utm_source=rss"), item_id("https://A.com/x"))

    def test_page_meta(self):
        html = """<html><head><meta property="og:image" content="/i/hero.jpg">
        <meta property="article:published_time" content="2026-10-14T09:00:00+09:00">
        <meta property="og:title" content="Honryu"><meta name="description" content="A limited pen."></head></html>"""
        m = page_meta(html, "https://sailor.co.jp/news/20261014/")
        self.assertEqual(m["image"], "https://sailor.co.jp/i/hero.jpg")
        self.assertEqual(m["date"], "2026-10-14")
        self.assertEqual(m["description"], "A limited pen.")


class ThumbTests(unittest.TestCase):
    def test_reencodes_to_webp(self):
        out = make_thumbnail(png_bytes(2000, 1200), width=960)
        self.assertEqual(sniff(out), "WEBP")
        with Image.open(io.BytesIO(out)) as im:
            self.assertEqual(im.size, (960, 576))
            self.assertNotIn("exif", im.info)

    def test_transparency_flattened(self):
        out = make_thumbnail(png_bytes(600, 400, "RGBA"))
        with Image.open(io.BytesIO(out)) as im:
            self.assertEqual(im.mode, "RGB")

    def test_rejects_svg_and_html(self):
        for data in (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', b"<!doctype html><p>hi"):
            with self.assertRaises(Rejected):
                make_thumbnail(data)

    def test_trailing_payload_is_dropped(self):
        payload = b"<script>evil()</script>" * 10
        out = make_thumbnail(png_bytes() + payload)
        self.assertNotIn(b"evil", out)

    def test_rejects_bombs_and_tiny(self):
        with self.assertRaises(Exception):
            make_thumbnail(png_bytes(9000, 9000), max_pixels=10_000_000)
        with self.assertRaises(Rejected):
            make_thumbnail(png_bytes(64, 64))


class SelectTests(unittest.TestCase):
    def _items(self):
        return [
            Item("CG Channel", "Maxon releases Redshift 2027.0", "https://cg/redshift", ["pipeline", "vfx"], "2026-10-18", "GPU renderer update."),
            Item("CG Channel", "Watch Raynault's VFX breakdown for Mayday", "https://cg/mayday", ["pipeline", "vfx"], "2026-10-20", "CG city."),
            Item("Sailor", "焔龍", "https://sailor/20261014", ["stationery"], "2026-10-14", lang="ja", weight=2),
            Item("Old", "Ancient news", "https://old/1", ["vfx"], "2026-08-01"),
            Item("Shown", "Already published", "https://shown/1", ["vfx"], "2026-10-20"),
        ]

    def test_shortlist_drops_old_and_shown(self):
        items = self._items()
        shown = {item_id("https://shown/1"): "2026-10-20"}
        out = shortlist(items, shown, date(2026, 10, 21), 10, 100)
        urls = {i.url for i in out}
        self.assertNotIn("https://old/1", urls)
        self.assertNotIn("https://shown/1", urls)
        self.assertEqual(len(out), 3)

    def test_plain_assigns_topics(self):
        _, stories = plain(shortlist(self._items(), {}, date(2026, 10, 21), 10, 100), ["obsidian", "stationery", "vfx", "pipeline"], 8)
        topic = {s.url: s.topic for s in stories}
        self.assertEqual(topic["https://cg/redshift"], "pipeline")
        self.assertEqual(topic["https://cg/mayday"], "vfx")
        self.assertEqual(topic["https://sailor/20261014"], "stationery")

    def test_parse_json_with_fences(self):
        self.assertEqual(_parse_json('```json\n{"headline": "x", "stories": []}\n```')["headline"], "x")


class RenderTests(unittest.TestCase):
    def test_render_escapes_and_links(self):
        import pline.render as render
        import pline.state as state

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            state.EDITIONS = tmp / "editions"
            render.list_editions = state.list_editions
            ed = {
                "date": "2026-10-21", "generated_at": "2026-10-21T06:40:00+13:00", "headline": "Big day",
                "sources_ok": 20, "sources_total": 22, "writer": None,
                "stories": [{"id": "abc", "topic": "vfx", "source": "Brew", "title": "<b>bold</b> & co", "url": "https://x/1",
                             "date": "2026-10-20", "image": "thumbs/a.webp", "summary": "S"}],
            }
            state.save_edition(ed)
            cfg = {"site": {"title": "Pline", "timezone": "Pacific/Auckland", "greeting": "Hi"},
                   "topics": [{"key": "vfx", "name": "VFX", "tagline": "t", "tone": "light"}]}
            render.render_all(cfg, out=tmp / "docs")
            html = (tmp / "docs" / "index.html").read_text(encoding="utf-8")
            self.assertIn("&lt;b&gt;bold&lt;/b&gt; &amp; co", html)
            self.assertIn('src="thumbs/a.webp"', html)
            self.assertIn("Yesterday", html)
            self.assertTrue((tmp / "docs" / "2026-10-21.html").exists())


if __name__ == "__main__":
    unittest.main()
