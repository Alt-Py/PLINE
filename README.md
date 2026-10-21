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
- **Design:** [`templates/page.html.j2`](templates/page.html.j2), styled after Apple's web design language: full-width tiles alternating light and dark, one blue accent, system fonts.
