"""Render editions to static HTML in docs/."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import DOCS, TEMPLATES
from .state import list_editions

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def long_date(s: str) -> str:
    d = date.fromisoformat(s)
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def short_day(s: str) -> str:
    d = date.fromisoformat(s)
    return f"{DAYS[d.weekday()][:3]} {d.day}"


def age(item_date: str | None, edition_date: str) -> str:
    if not item_date:
        return "Recent"
    a, b = date.fromisoformat(item_date), date.fromisoformat(edition_date)
    days = (b - a).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    return f"{a.day} {MONTHS[a.month - 1][:3]}" + ("" if a.year == b.year else f" {a.year}")


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(long_date=long_date, short_day=short_day)
    env.globals.update(age=age)
    return env


def render_all(cfg: dict, out: Path = DOCS) -> list[Path]:
    editions = list_editions()
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("")
    tpl = _env().get_template("page.html.j2")
    written = []
    nav = [{"date": e["date"], "file": f"{e['date']}.html"} for e in editions[:14]]
    for i, ed in enumerate(editions):
        html = tpl.render(
            site=cfg["site"],
            topics=cfg["topics"],
            ed=ed,
            nav=nav,
            latest=(i == 0),
            newer=editions[i - 1]["date"] if i > 0 else None,
            older=editions[i + 1]["date"] if i + 1 < len(editions) else None,
        )
        path = out / f"{ed['date']}.html"
        path.write_text(html, encoding="utf-8")
        written.append(path)
        if i == 0:
            (out / "index.html").write_text(html, encoding="utf-8")
            written.append(out / "index.html")
    if not editions:
        (out / "index.html").write_text(
            tpl.render(site=cfg["site"], topics=cfg["topics"], ed=None, nav=[], latest=True, newer=None, older=None),
            encoding="utf-8",
        )
    return written
