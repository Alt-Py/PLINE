"""Learn Thomas's taste from the likes he leaves on the page.

The page posts votes as comments on one locked GitHub issue. Each daily run
reads new comments, keeps the latest vote per story in state/votes.json, and
turns them into preferences: a weight multiplier per source, liked and
disliked keywords, and example titles that Claude reads when choosing stories.

Only comments written by the repository owner, members or collaborators are
counted, so nobody else can steer the page.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone

import requests

from .state import STATE, load_json, save_json

log = logging.getLogger("pline.feedback")

VOTES = STATE / "votes.json"
PREFS = STATE / "preferences.json"
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
BLOCK = re.compile(r"```json\s*(.*?)```", re.S)

STOP = set(
    """the and for with from that this into your what when will have more new now its are was were how why who
    out off over just than then them they their about after before also onto upon via vs our you all can get
    video breakdown review released release releases update first look watch here edition 2025 2026 2027
    les des une pour avec dans""".split()
)


def fetch_votes(repo: str, issue: int, token: str | None) -> int:
    """Pull new vote comments into state/votes.json. Returns how many votes were read."""
    store = load_json(VOTES, {"last_comment_id": 0, "votes": {}})
    if not token or not repo or not issue:
        return 0
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    url = f"https://api.github.com/repos/{repo}/issues/{issue}/comments?per_page=100"
    read = 0
    last = store["last_comment_id"]
    while url:
        r = s.get(url, timeout=20)
        if r.status_code != 200:
            log.warning("could not read likes: HTTP %s", r.status_code)
            break
        for c in r.json():
            if c["id"] <= store["last_comment_id"] or c.get("author_association") not in TRUSTED:
                continue
            last = max(last, c["id"])
            for block in BLOCK.findall(c.get("body") or ""):
                try:
                    payload = json.loads(block)
                except ValueError:
                    continue
                for v in payload.get("votes", []) if isinstance(payload, dict) else []:
                    if not isinstance(v, dict) or not isinstance(v.get("id"), str):
                        continue
                    vote = int(v.get("vote", 0))
                    if vote not in (-1, 0, 1):
                        continue
                    store["votes"][v["id"][:40]] = {
                        "vote": vote,
                        "source": str(v.get("source", ""))[:80],
                        "topic": str(v.get("topic", ""))[:20],
                        "title": str(v.get("title", ""))[:200],
                        "at": str(v.get("at") or c.get("created_at", ""))[:25],
                    }
                    read += 1
        url = r.links.get("next", {}).get("url")
    store["last_comment_id"] = last
    store["votes"] = {k: v for k, v in store["votes"].items() if v["vote"] != 0}
    save_json(VOTES, store)
    log.info("read %d new votes; %d stories rated in total", read, len(store["votes"]))
    return read


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9+.-]{2,}|[぀-ヿ一-鿿]{2,}", text.lower()) if w not in STOP}


def build_preferences() -> dict:
    votes = list(load_json(VOTES, {"votes": {}})["votes"].values())
    by_source: Counter = Counter()
    terms: Counter = Counter()
    for v in votes:
        by_source[v["source"]] += v["vote"]
        for w in words(v["title"]):
            terms[w] += v["vote"]
    sources = {s: round(min(2.0, max(0.4, 1 + 0.12 * n)), 2) for s, n in by_source.items() if n}
    newest = sorted(votes, key=lambda v: v["at"], reverse=True)
    prefs = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "likes": sum(1 for v in votes if v["vote"] > 0),
        "dislikes": sum(1 for v in votes if v["vote"] < 0),
        "source_weight": sources,
        "liked_terms": [w for w, n in terms.most_common() if n >= 2][:30],
        "disliked_terms": [w for w, n in sorted(terms.items(), key=lambda x: x[1]) if n <= -2][:30],
        "liked_titles": [f"[{v['topic']}] {v['title']}" for v in newest if v["vote"] > 0][:40],
        "disliked_titles": [f"[{v['topic']}] {v['title']}" for v in newest if v["vote"] < 0][:40],
    }
    save_json(PREFS, prefs)
    return prefs


def load_preferences() -> dict:
    return load_json(PREFS, {})


def taste_score(title: str, source: str, prefs: dict) -> float:
    """Multiplier applied to a story's rank before the shortlist is cut."""
    if not prefs:
        return 1.0
    mult = prefs.get("source_weight", {}).get(source, 1.0)
    w = words(title)
    liked = len(w & set(prefs.get("liked_terms", [])))
    disliked = len(w & set(prefs.get("disliked_terms", [])))
    return mult * math.pow(1.15, min(liked, 3)) * math.pow(0.85, min(disliked, 3))


def taste_prompt(prefs: dict) -> str:
    """A short brief appended to Claude's instructions."""
    if not prefs or not (prefs.get("likes") or prefs.get("dislikes")):
        return ""
    lines = ["", "THOMAS'S TASTE (learned from his likes on the page — use it to choose and order, but keep variety):"]
    if prefs.get("liked_titles"):
        lines.append("Stories he liked recently:\n" + "\n".join(f"  + {t}" for t in prefs["liked_titles"][:25]))
    if prefs.get("disliked_titles"):
        lines.append("Stories he wanted less of:\n" + "\n".join(f"  - {t}" for t in prefs["disliked_titles"][:25]))
    fav = [s for s, m in prefs.get("source_weight", {}).items() if m > 1]
    meh = [s for s, m in prefs.get("source_weight", {}).items() if m < 1]
    if fav:
        lines.append("Sources he tends to like: " + ", ".join(fav))
    if meh:
        lines.append("Sources he tends to skip: " + ", ".join(meh))
    return "\n".join(lines)


def settings(cfg: dict) -> tuple[str, int, str | None]:
    fb = cfg.get("feedback", {})
    return fb.get("repo", ""), int(fb.get("issue", 0)), os.environ.get("GITHUB_TOKEN")
