# Pline

A morning news page for four interests: **Obsidian**, **Japanese stationery**, the **VFX industry**, and **Houdini, RenderMan & USD**. Named after Pliny the Elder (*Pline l'Ancien*), who tried to write everything down.

**Read it:** https://alt-py.github.io/PLINE/

## How it works

Every morning at 06:40 Wellington time, GitHub Actions runs `python -m pline run`:

1. **Collect.** Reads every source in [`sources.yaml`](sources.yaml): RSS and Atom feeds, GitHub release feeds, and news listing pages (Sailor, Pilot, SideFX, RenderMan, AOUSD…).
2. **Shortlist.** Drops stories already published, older than 10 days, or duplicated, then ranks by source weight and freshness.
3. **Enrich.** Opens up to 90 article pages (only where `robots.txt` allows) to find each one's preview image, date and description.
4. **Write.** If the `ANTHROPIC_API_KEY` secret is set, Claude picks the stories that fit each section, merges duplicates, translates Japanese titles and writes short summaries. Without the key, Pline still publishes, using the feeds' own titles and excerpts.
5. **Pictures.** Each chosen story's preview image is downloaded and re-drawn as a small WebP thumbnail (see below).
6. **Publish.** Renders static HTML into `docs/`, commits the edition to `state/`, and deploys to GitHub Pages. Editions are kept for 30 days.

Other workflows:
- **Test and republish** runs on every push: tests, then re-renders the stored editions (no news fetched, no Claude call). Use it after changing the design.
- **Check sources** runs every Monday and fails if a required source stops answering.

## Picture safety

Every image is treated as untrusted ([`pline/thumbs.py`](pline/thumbs.py)):

- Only JPEG, PNG, WebP and GIF are accepted, recognised by their first bytes, never by name or the server's claimed type. SVG is always refused because it can contain code.
- Downloads stop at 6 MB, and images over 40 megapixels are refused before decoding.
- The image is decoded and re-drawn as a brand-new WebP. Only pixels survive; metadata and anything hidden in the original are discarded. The original file is never saved.
- All of this happens on GitHub's disposable runners, not on your computer. Your browser then loads only the clean thumbnails from this site, so the original sites never see your visits.

## Setup

1. **Pages:** Settings → Pages → Source: **GitHub Actions**.
2. **Claude key (recommended):** Settings → Secrets and variables → Actions → New repository secret, named `ANTHROPIC_API_KEY`. Create the key at console.anthropic.com. One edition is a single request of a few thousand tokens.
3. **First edition:** Actions → *Daily edition* → *Run workflow*. Tick *Rebuild today's edition from scratch* to redo today's edition, for example after adding the key.

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
