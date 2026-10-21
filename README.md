# Pline

A morning news page for four interests: **Obsidian**, **Japanese stationery**, the **VFX industry**, and **Houdini, RenderMan & USD**.

**Read it:** https://alt-py.github.io/PLINE/

## Commands

```bash
pip install -r requirements.txt
python -m pline run --dry      # print today's shortlist, write nothing
python -m pline run --no-llm   # full run without Claude
python -m pline run --fresh    # rebuild today's edition
python -m pline render         # re-render stored editions, no network
python -m pline validate       # check every source
python -m unittest discover -s tests -v
```

## Changing things

- **Sources:** edit [`sources.yaml`](sources.yaml). `weight` raises or lowers a source; `optional: true` sources may fail without turning the Monday check red.
- **Sections, timezone, limits, model:** [`config.yaml`](config.yaml).
- **Design:** [`templates/page.html.j2`](templates/page.html.j2) and [`templates/app.js`](templates/app.js), styled after Apple's web design language in a single dark palette.

## Likes

The 👍 / 👎 buttons on each card post votes to the locked [issue #1](https://github.com/Alt-Py/PLINE/issues/1) using a fine-grained token (PLINE only, Issues read/write) kept in each browser. The daily run counts only comments from the repo owner or collaborators and learns source weights, liked and disliked subjects, and example titles for Claude ([`pline/feedback.py`](pline/feedback.py)).

