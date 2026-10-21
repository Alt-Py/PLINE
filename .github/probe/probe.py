"""One-off check (round 2), max 9 notices."""
import json, subprocess, time
import requests

UA = {"User-Agent": "PlineBot/1.0 (+https://github.com/Alt-Py/PLINE)"}
def note(title, msg): print(f"::notice title={title}::{msg}".replace("\n", " ")[:900])
now = int(time.time())

# Arctic Shift with limit=100 and 12 h windows, 36-72 h old
for sub in ["fountainpens", "ObsidianMD", "vfx"]:
    parts, total, best = [], 0, []
    for k in range(3):
        before, after = now - (36 + 12 * k) * 3600, now - (48 + 12 * k) * 3600
        try:
            r = requests.get("https://arctic-shift.photon-reddit.com/api/posts/search", headers=UA, timeout=40, params={
                "subreddit": sub, "after": after, "before": before, "limit": 100,
                "fields": "title,score,num_comments,created_utc,retrieved_on"})
            d = r.json().get("data", []) if r.ok else []
            parts.append(f"{r.status_code}:{len(d)}" + ("" if r.ok else ":" + r.text[:60]))
            total += len(d); best += d
        except Exception as e:
            parts.append(type(e).__name__)
        time.sleep(1)
    top = sorted(best, key=lambda p: p.get("score", 0), reverse=True)[:3]
    gap = max([(p.get("retrieved_on") or 0) - p.get("created_utc", 0) for p in best] or [0]) / 3600
    note(f"ArcticShift r/{sub}", f"windows {parts}, {total} posts, refresh gap up to {gap:.0f}h, top: " + " | ".join(f"{p['score']}↑ {p['title'][:45]}" for p in top))

# Invidious + Piped, all in one line each
try:
    inst = requests.get("https://api.invidious.io/instances.json?sort_by=type,users", headers=UA, timeout=20).json()
    hosts = [i[1]["uri"] for i in inst if i[1].get("api") and i[1].get("type") == "https"][:10]
except Exception as e:
    hosts = []
res = []
for h in hosts:
    try:
        r = requests.get(h + "/api/v1/search", headers=UA, timeout=15, params={"q": "万年筆 インク", "sort_by": "upload_date", "type": "video"})
        n = len(r.json()) if r.ok and "json" in r.headers.get("content-type", "") else 0
        res.append(f"{h.split('//')[1]}={r.status_code}/{n}")
    except Exception as e:
        res.append(f"{h.split('//')[1]}={type(e).__name__}")
note("Invidious", f"{len(hosts)} listed with API: " + ", ".join(res))

res = []
for h in ["https://api.piped.private.coffee", "https://pipedapi.adminforge.de", "https://pipedapi.leptons.xyz", "https://pipedapi.r4fo.com", "https://piped-api.lunar.icu"]:
    try:
        r = requests.get(h + "/search", headers=UA, timeout=15, params={"q": "obsidian.md plugin", "filter": "videos"})
        items = r.json().get("items", []) if r.ok else []
        res.append(f"{h.split('//')[1]}={r.status_code}/{len(items)}")
        if items and "sample" not in locals():
            sample = items[0]
    except Exception as e:
        res.append(f"{h.split('//')[1]}={type(e).__name__}")
note("Piped", ", ".join(res) + (f" · sample keys: {sorted(sample.keys())[:14]}, uploaded={sample.get('uploadedDate')} views={sample.get('views')}" if "sample" in locals() else ""))

# yt-dlp search
try:
    out = subprocess.run(["yt-dlp", "--flat-playlist", "-J", "ytsearchdate8:houdini solaris tutorial"], capture_output=True, text=True, timeout=90)
    entries = json.loads(out.stdout).get("entries", []) if out.returncode == 0 else []
    note("yt-dlp search", f"exit {out.returncode}, {len(entries)} videos: " + " | ".join(f"{e.get('title','')[:40]} ({e.get('view_count')} views)" for e in entries[:3]) + (" " + out.stderr[-200:] if out.returncode else ""))
except Exception as e:
    note("yt-dlp search", f"ERROR {type(e).__name__}: {e}"[:200])
