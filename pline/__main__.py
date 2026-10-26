"""Command line: python -m pline {run,render,validate}."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

from . import ROOT
from .collect import SKIPPED, collect_all
from .enrich import enrich, image_for
from .feedback import build_preferences, fetch_votes, settings as feedback_settings
from .net import Http
from .render import render_all
from .select import diversify, mark_extras, plain, shortlist, with_claude
from .state import THUMBS, list_editions, load_shown, prune, save_edition, save_shown
from .thumbs import fetch_thumbnail

log = logging.getLogger("pline")


def load_config() -> tuple[dict, list[dict]]:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    sources = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    return cfg, sources


def cmd_run(args) -> int:
    cfg, sources = load_config()
    tz = ZoneInfo(cfg["site"]["timezone"])
    now = datetime.now(tz)
    today = now.date()
    topics = [t["key"] for t in cfg["topics"]]
    ed_cfg = cfg["edition"]
    http = Http(cfg["http"])

    repo, issue, gh_token = feedback_settings(cfg)
    try:
        fetch_votes(repo, issue, gh_token)
    except Exception as exc:
        log.warning("could not read likes: %s", exc)
    prefs = build_preferences()
    log.info("taste: %d likes, %d dislikes", prefs.get("likes", 0), prefs.get("dislikes", 0))

    items, report = collect_all(http, sources)
    active = {k: v for k, v in report.items() if not (v or "").startswith(SKIPPED)}
    ok = sum(1 for e in active.values() if e is None)
    log.info("collected %d stories from %d/%d sources", len(items), ok, len(active))

    if args.if_missing and (ROOT / "state" / "editions" / f"{today.isoformat()}.json").exists():
        log.info("today's edition (%s) is already published; nothing to do", today.isoformat())
        return 0

    shown = load_shown()
    if args.fresh:
        # Rebuild today's edition from scratch, e.g. after adding the Claude key.
        shown = {k: v for k, v in shown.items() if v != today.isoformat()}
        (ROOT / "state" / "editions" / f"{today.isoformat()}.json").unlink(missing_ok=True)
    # Rank once without dates to choose which article pages to read, then again with them.
    pre = shortlist(items, shown, today, ed_cfg["window_days"], limit=10_000, prefs=prefs)
    enrich(http, pre, cfg["http"].get("enrich_limit", 90))
    cands = shortlist(pre, shown, today, ed_cfg["window_days"], limit=cfg["llm"].get("max_candidates", 140), prefs=prefs)
    log.info("shortlisted %d candidates", len(cands))

    if args.dry:
        for it in cands:
            print(f"{it.topics[0]:10} {it.date or '----------'} {it.source:24} {it.title[:90]}")
        return 0

    per_topic = ed_cfg["max_per_topic"] + ed_cfg.get("extra_per_topic", 0)
    result = None if args.no_llm else with_claude(cands, topics, cfg["llm"], per_topic, prefs, ed_cfg["max_per_topic"])
    writer = cfg["llm"].get("model") if result else None
    headline, stories = result or plain(cands, topics, per_topic)
    stories = diversify(stories, ed_cfg.get("per_source", 3), ed_cfg["max_per_topic"])
    mark_extras(stories, ed_cfg["max_per_topic"])

    for st in stories:
        url = getattr(st, "_image_url", None) or image_for(http, st.url)
        name = fetch_thumbnail(http, url, THUMBS, cfg["thumbs"]) if url else None
        st.image = f"thumbs/{name}" if name else None
    log.info("%d of %d stories have a picture", sum(1 for s in stories if s.image), len(stories))

    edition = {
        "date": today.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "headline": headline,
        "writer": writer,
        "sources_ok": ok,
        "sources_total": len(active),
        "source_errors": {k: v for k, v in active.items() if v},
        "taste": {"likes": prefs.get("likes", 0), "dislikes": prefs.get("dislikes", 0)},
        "stories": [s.to_dict() for s in stories],
    }
    # A second run on the same day keeps what the first run already published.
    previous = next((e for e in list_editions() if e["date"] == edition["date"]), None)
    if previous:
        known = {s["id"] for s in edition["stories"]}
        edition["stories"] = [s for s in previous.get("stories", []) if s["id"] not in known] + edition["stories"]
    save_edition(edition)

    for st in stories:
        for member in getattr(st, "_members", [st.id]):
            shown.setdefault(member, edition["date"])
    save_shown(shown, today)
    prune(today, ed_cfg["keep_days"])
    render_all(cfg)
    log.info("published %s with %d stories", edition["date"], len(edition["stories"]))
    return 0


def cmd_render(args) -> int:
    cfg, _ = load_config()
    for p in render_all(cfg):
        log.info("wrote %s", p.relative_to(ROOT))
    return 0


def cmd_validate(args) -> int:
    cfg, sources = load_config()
    http = Http(cfg["http"])
    _, report = collect_all(http, sources)
    def failed(name):
        err = report.get(name)
        return bool(err) and not err.startswith(SKIPPED)

    required_broken = [s["name"] for s in sources if failed(s["name"]) and not s.get("optional")]
    print("\nSource check")
    for s in sources:
        err = report.get(s["name"])
        flag = "ok " if not err else ("off" if err.startswith(SKIPPED) else ("opt" if s.get("optional") else "BAD"))
        print(f"  [{flag}] {s['name']:28} {err or ''}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("| Source | Status |\n|---|---|\n")
            for s in sources:
                err = report.get(s["name"])
                f.write(f"| {s['name']} | {'✅' if not err else ('⏸ ' if err.startswith(SKIPPED) else '⚠️ optional: ' if s.get('optional') else '❌ ') + str(err)} |\n")
    return 1 if required_broken else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pline")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="collect, write and publish today's edition")
    r.add_argument("--dry", action="store_true", help="print the shortlist and stop")
    r.add_argument("--no-llm", action="store_true", help="skip Claude even if a key is set")
    r.add_argument("--fresh", action="store_true", help="rebuild today's edition from scratch")
    r.add_argument("--if-missing", action="store_true", help="do nothing if today's edition already exists (scheduled retries)")
    sub.add_parser("render", help="re-render every stored edition (no network)")
    sub.add_parser("validate", help="check that every source still answers")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s", stream=sys.stderr)
    return {"run": cmd_run, "render": cmd_render, "validate": cmd_validate}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
