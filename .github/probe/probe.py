"""One-off check: which no-account Reddit / YouTube routes work from GitHub runners."""
import json, subprocess, time
import requests

UA = {"User-Agent": "PlineBot/1.0 (+https://github.com/Alt-Py/PLINE)"}
def note(title, msg): print(f"::notice title={title}::{msg}".replace("\n", " "))

now = int(time.time())
# 1. Arctic Shift: posts 36-72 h old, ranked by refreshed score
for sub in ["fountainpens", "ObsidianMD", "vfx", "Houdini"]:
    try:
        r = requests.get("https://arctic-shift.photon-reddit.com/api/posts/search", headers=UA, timeout=30, params={
            "subreddit": sub, "after": now - 72 * 3600, "before": now - 36 * 3600, "limit": "auto",
            "fields": "title,score,num_comments,created_utc,retrieved_on"})
        d = r.json().get("data", []) if r.ok else []
        top = sorted(d, key=lambda p: p.get("score", 0), reverse=True)[:3]
        gaps = [round((p.get("retrieved_on", 0) - p.get("created_utc", 0)) / 3600) for p in d[:20]]
        note(f"ArcticShift r/{sub}", f"HTTP {r.status_code}, {len(d)} posts, max refresh gap {max(gaps or [0])}h, top: " +
             " | ".join(f"{p['score']}↑ {p['title'][:50]}" for p in top))
    except Exception as e:
        note(f"ArcticShift r/{sub}", f"ERROR {type(e).__name__}: {e}"[:200])

# 2. Reddit itself, no account
for url in ["https://www.reddit.com/r/fountainpens/top.json?t=day&limit=5",
            "https://old.reddit.com/r/fountainpens/top/.rss?t=day",
            "https://www.reddit.com/r/fountainpens/top/.rss?t=day"]:
    try:
        r = requests.get(url, headers=UA, timeout=20)
        note("Reddit direct", f"{url.split('.com')[0][8:]}…{url[-30:]} → HTTP {r.status_code}, {len(r.content)} bytes")
    except Exception as e:
        note("Reddit direct", f"{url[-40:]} ERROR {type(e).__name__}")

# 3. Invidious public instances (YouTube API proxies)
try:
    inst = requests.get("https://api.invidious.io/instances.json?sort_by=type,users", headers=UA, timeout=20).json()
    hosts = [i[1]["uri"] for i in inst if i[1].get("api") and i[1].get("type") == "https"][:8]
except Exception as e:
    hosts = []; note("Invidious", f"instance list ERROR {e}"[:200])
ok = []
for h in hosts:
    try:
        r = requests.get(h + "/api/v1/search", headers=UA, timeout=15, params={"q": "houdini solaris", "sort_by": "upload_date", "type": "video"})
        n = len(r.json()) if r.ok and r.headers.get("content-type", "").startswith("application/json") else 0
        note("Invidious", f"{h} → HTTP {r.status_code}, {n} videos")
        if n: ok.append(h)
    except Exception as e:
        note("Invidious", f"{h} → {type(e).__name__}")

# 4. Piped public API
for h in ["https://pipedapi.kavin.rocks", "https://api.piped.private.coffee", "https://pipedapi.adminforge.de"]:
    try:
        r = requests.get(h + "/search", headers=UA, timeout=15, params={"q": "houdini solaris", "filter": "videos"})
        n = len(r.json().get("items", [])) if r.ok else 0
        note("Piped", f"{h} → HTTP {r.status_code}, {n} videos")
    except Exception as e:
        note("Piped", f"{h} → {type(e).__name__}")

# 5. yt-dlp search listing (no download, no account)
try:
    out = subprocess.run(["yt-dlp", "--flat-playlist", "-J", "ytsearchdate8:houdini solaris tutorial"],
                         capture_output=True, text=True, timeout=90)
    entries = json.loads(out.stdout).get("entries", []) if out.returncode == 0 else []
    note("yt-dlp search", f"exit {out.returncode}, {len(entries)} videos: " +
         " | ".join(f"{e.get('title','')[:40]} ({e.get('view_count')} views)" for e in entries[:3]) + (" " + out.stderr[-150:] if out.returncode else ""))
except Exception as e:
    note("yt-dlp search", f"ERROR {type(e).__name__}: {e}"[:200])
