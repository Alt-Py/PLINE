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


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        import pline.feedback as fb

        self.fb = fb
        self.tmp = tempfile.TemporaryDirectory()
        fb.VOTES = Path(self.tmp.name) / "votes.json"
        fb.PREFS = Path(self.tmp.name) / "preferences.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _comments(self):
        def body(votes):
            return "Pline votes\n\n```json\n" + json.dumps({"votes": votes}) + "\n```"

        return [
            {"id": 10, "author_association": "OWNER", "created_at": "2026-10-21T08:00:00Z", "body": body([
                {"id": "a1", "vote": 1, "source": "Sailor", "topic": "stationery", "title": "Sailor ebonite fountain pen nagamaki"},
                {"id": "a2", "vote": 1, "source": "Sailor", "topic": "stationery", "title": "New ebonite fountain pen from Platinum"},
                {"id": "a3", "vote": -1, "source": "r/vfx", "topic": "vfx", "title": "Layoffs rumour thread"},
                {"id": "a4", "vote": -1, "source": "r/vfx", "topic": "vfx", "title": "Another layoffs rumour"},
            ])},
            {"id": 11, "author_association": "NONE", "created_at": "2026-10-21T09:00:00Z", "body": body([
                {"id": "a3", "vote": 1, "source": "r/vfx", "topic": "vfx", "title": "stranger tries to steer"},
            ])},
        ]

    def test_only_trusted_votes_count_and_shape_preferences(self):
        from unittest import mock

        resp = mock.Mock(status_code=200, links={})
        resp.json.return_value = self._comments()
        with mock.patch("pline.feedback.requests.Session") as S:
            S.return_value.get.return_value = resp
            self.fb.fetch_votes("Alt-Py/PLINE", 1, "token")
        votes = json.loads(self.fb.VOTES.read_text())["votes"]
        self.assertEqual(votes["a3"]["vote"], -1)  # the stranger's comment was ignored
        prefs = self.fb.build_preferences()
        self.assertEqual((prefs["likes"], prefs["dislikes"]), (2, 2))
        self.assertGreater(prefs["source_weight"]["Sailor"], 1)
        self.assertLess(prefs["source_weight"]["r/vfx"], 1)
        self.assertIn("ebonite", prefs["liked_terms"])
        self.assertIn("layoffs", prefs["disliked_terms"])
        self.assertGreater(self.fb.taste_score("Pilot ebonite fountain pen", "Pilot", prefs), 1)
        self.assertLess(self.fb.taste_score("layoffs rumour", "r/vfx", prefs), 1)
        self.assertIn("Sailor ebonite", self.fb.taste_prompt(prefs))

    def test_no_votes_is_neutral(self):
        prefs = self.fb.build_preferences()
        self.assertEqual(self.fb.taste_prompt(prefs), "")
        self.assertEqual(self.fb.taste_score("anything", "x", {}), 1.0)


class SocialSourceTests(unittest.TestCase):
    def test_reddit_listing(self):
        from pline.collect import parse_reddit_listing

        data = {"data": {"children": [
            {"data": {"title": "My Sailor &amp; Pilot inks", "permalink": "/r/fountainpens/comments/x/y/", "score": 250,
                      "num_comments": 40, "subreddit": "fountainpens", "created_utc": 1792560000,
                      "preview": {"images": [{"source": {"url": "https://preview.redd.it/a.jpg?w=1&amp;s=2"}}]}}},
            {"data": {"title": "low score", "permalink": "/r/x/2", "score": 3, "subreddit": "fountainpens", "created_utc": 1792560000}},
            {"data": {"title": "pinned", "permalink": "/r/x/3", "score": 999, "stickied": True, "subreddit": "fountainpens", "created_utc": 1792560000}},
        ]}}
        items = parse_reddit_listing(data, {"subreddit": "fountainpens", "topics": ["stationery"], "min_score": 60})
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it.title, "My Sailor & Pilot inks")
        self.assertEqual(it.kind, "reddit")
        self.assertEqual(it.source, "r/fountainpens")
        self.assertEqual(it.image_url, "https://preview.redd.it/a.jpg?w=1&s=2")
        self.assertIn("250 upvotes", it.excerpt)

    def test_invidious_and_piped_parsing(self):
        import time as _t
        from pline.collect import days_ago, parse_invidious, parse_piped

        src = {"topics": ["pipeline"], "lang": "en"}
        now = int(_t.time())
        inv = [
            {"type": "video", "videoId": "abcdefghijk", "title": "Solaris &amp; Karma", "author": "Entagma", "published": now - 2 * 86400,
             "lengthSeconds": 900, "viewCount": 12345, "description": "Lighting in Solaris"},
            {"type": "video", "videoId": "shortshort1", "title": "short", "author": "x", "published": now, "lengthSeconds": 40},
            {"type": "video", "videoId": "oldoldoldol", "title": "old", "author": "x", "published": now - 90 * 86400, "lengthSeconds": 600},
            {"type": "channel", "author": "x"},
        ]
        items = parse_invidious(inv, src, 7)
        self.assertEqual([i.url for i in items], ["https://www.youtube.com/watch?v=abcdefghijk"])
        self.assertEqual(items[0].title, "Solaris & Karma")
        self.assertEqual(items[0].image_url, "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg")
        self.assertIn("12,345 views", items[0].excerpt)
        self.assertEqual(items[0].kind, "video")

        piped = [
            {"url": "/watch?v=ZYXWVUTSRQP", "title": "Obsidian Bases", "uploaderName": "Nicole", "uploaded": -1, "uploadedDate": "3 days ago", "duration": 700, "isShort": False, "views": 900},
            {"url": "/watch?v=AAAAAAAAAAA", "title": "too old", "uploaderName": "n", "uploaded": -1, "uploadedDate": "1 year ago", "duration": 700},
            {"url": "/watch?v=BBBBBBBBBBB", "title": "short", "uploaderName": "n", "uploaded": now * 1000, "duration": 50, "isShort": True},
        ]
        self.assertEqual([i.title for i in parse_piped(piped, src, 7)], ["Obsidian Bases"])
        self.assertEqual(days_ago("2 weeks ago"), 14)
        self.assertIsNone(days_ago("Streamed"))

    def test_arctic_shift_ranks_by_score(self):
        from unittest import mock
        from pline.collect import arctic_shift

        posts = [{"title": "small", "score": 70, "num_comments": 3, "permalink": "/r/fountainpens/comments/1/a/", "created_utc": 1792500000, "subreddit": "fountainpens"},
                 {"title": "big", "score": 871, "num_comments": 90, "permalink": "/r/fountainpens/comments/2/b/", "created_utc": 1792500000, "subreddit": "fountainpens"},
                 {"title": "tiny", "score": 4, "permalink": "/r/fountainpens/comments/3/c/", "created_utc": 1792500000, "subreddit": "fountainpens"}]
        busy = mock.Mock(ok=False, status_code=422, text='{"error":"Timeout. Maybe slow down a bit"}')
        good = mock.Mock(ok=True, status_code=200)
        good.json.return_value = {"data": posts}
        http = mock.Mock()
        http.session.get.side_effect = [busy, good]
        with mock.patch("pline.collect.time.sleep"):
            items = arctic_shift(http, {"subreddit": "fountainpens", "topics": ["stationery"], "min_score": 60})
        self.assertEqual([i.title for i in items], ["big", "small"])
        self.assertIn("871 upvotes", items[0].excerpt)


class ShowMoreTests(unittest.TestCase):
    def test_extras_marked_per_section_and_rendered_hidden(self):
        from pline.model import Story
        from pline.select import mark_extras
        import pline.render as render
        import pline.state as state

        stories = [Story(id=f"v{i}", topic="vfx", source="s", title=f"t{i}", url=f"https://x/{i}") for i in range(5)]
        stories.append(Story(id="o1", topic="obsidian", source="s", title="o", url="https://x/o"))
        mark_extras(stories, 3)
        self.assertEqual([s.extra for s in stories], [False, False, False, True, True, False])
        self.assertNotIn("extra", stories[0].to_dict())

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            state.EDITIONS = tmp / "editions"
            render.list_editions = state.list_editions
            state.save_edition({"date": "2026-10-21", "generated_at": "2026-10-21T06:40:00+13:00", "headline": "h",
                                "sources_ok": 1, "sources_total": 1, "stories": [s.to_dict() for s in stories]})
            cfg = {"site": {"title": "Pline", "timezone": "Pacific/Auckland", "greeting": "Hi"},
                   "topics": [{"key": "vfx", "name": "VFX", "tagline": "t", "tone": "light"},
                              {"key": "obsidian", "name": "Obsidian", "tagline": "t", "tone": "dark"}]}
            render.render_all(cfg, out=tmp / "docs")
            html = (tmp / "docs" / "index.html").read_text(encoding="utf-8")
            self.assertEqual(html.count('extra" hidden'), 2)
            self.assertIn("Show 2 more stories", html)
            self.assertIn("3 stories · 2 more in reserve", html)
            self.assertEqual(html.count('class="pill more"'), 1)


class SavedTests(unittest.TestCase):
    def setUp(self):
        import pline.feedback as fb

        self.fb = fb
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        fb.VOTES, fb.PREFS, fb.SAVED = t / "votes.json", t / "preferences.json", t / "saved.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_last_writer_wins_and_fields_are_cleaned(self):
        items = {}
        story = {"id": "s1", "title": "Honryu", "url": "https://sailor.co.jp/news/20261014/", "image": "thumbs/0123456789abcdef.webp",
                 "topic": "stationery", "source": "Sailor", "evil": "<script>"}
        self.fb.apply_save(items, {"id": "s1", "saved": True, "at": "2026-10-22T08:00:00Z", "story": story})
        self.fb.apply_save(items, {"id": "s1", "saved": False, "at": "2026-10-22T07:00:00Z"})  # older: ignored
        self.assertTrue(items["s1"]["saved"])
        self.assertNotIn("evil", items["s1"])
        self.fb.apply_save(items, {"id": "s1", "saved": False, "at": "2026-10-22T09:00:00Z"})
        self.assertFalse(items["s1"]["saved"])
        self.assertEqual(items["s1"]["title"], "Honryu")  # story kept from the earlier event
        self.fb.apply_save(items, {"id": "s2", "saved": True, "at": "x", "story": {"title": "t", "url": "javascript:alert(1)", "image": "../../etc/passwd"}})
        self.assertNotIn("url", items["s2"])
        self.assertNotIn("image", items["s2"])

    def test_saves_read_from_comments_and_count_as_likes(self):
        from unittest import mock

        body = "x\n```json\n" + json.dumps({"saves": [{"id": "s9", "saved": True, "at": "2026-10-22T08:00:00Z",
                                                       "story": {"title": "Ebonite pen", "source": "Sailor", "topic": "stationery", "url": "https://a/b"}}]}) + "\n```"
        resp = mock.Mock(status_code=200, links={})
        resp.json.return_value = [{"id": 5, "author_association": "OWNER", "created_at": "2026-10-22T08:00:05Z", "body": body}]
        with mock.patch("pline.feedback.requests.Session") as S:
            S.return_value.get.return_value = resp
            self.fb.fetch_votes("Alt-Py/PLINE", 1, "token")
        lst = self.fb.saved_list()
        self.assertEqual([i["id"] for i in lst["items"]], ["s9"])
        self.assertEqual(lst["updated"], "2026-10-22T08:00:05Z")
        self.assertEqual(self.fb.build_preferences()["likes"], 1)

    def test_saved_page_renders(self):
        import pline.render as render
        import pline.state as state

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            state.EDITIONS = tmp / "editions"
            render.list_editions = state.list_editions
            state.save_edition({"date": "2026-10-22", "generated_at": "2026-10-22T06:40:00+13:00", "headline": "h", "sources_ok": 1, "sources_total": 1,
                                "stories": [{"id": "a", "topic": "vfx", "source": "S", "title": "T </script><b>", "url": "https://x/1", "extra": False}]})
            cfg = {"site": {"title": "Pline", "timezone": "Pacific/Auckland", "greeting": "Hi"}, "feedback": {"repo": "Alt-Py/PLINE", "issue": 1},
                   "topics": [{"key": "vfx", "name": "VFX", "tagline": "t", "tone": "light"}]}
            render.render_all(cfg, out=tmp / "docs")
            page = (tmp / "docs" / "index.html").read_text(encoding="utf-8")
            saved = (tmp / "docs" / "saved.html").read_text(encoding="utf-8")
            self.assertIn('id="pline-stories"', page)
            self.assertNotIn("</script><b>", page.split('id="pline-stories"')[1].split("</script>")[0] + "</script>"[:0])
            self.assertIn('class="vote save"', page)
            self.assertIn('href="saved.html"', page)
            self.assertIn('aria-current="page"', page)            # today's pill
            self.assertIn('data-page="saved"', saved)
            self.assertIn('id="savedGrid"', saved)
            self.assertNotIn('id="hideRead"', saved)
            self.assertTrue((tmp / "docs" / "saved.json").exists())
