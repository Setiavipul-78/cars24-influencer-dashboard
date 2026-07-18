#!/usr/bin/env python3
"""
Refresh script for India YouTube influencer data.
Mirrors refresh_au_uae.py — reads india_yt_data.json (which already contains
the creator list), scrapes fresh stats for video links via Apify YouTube scraper,
freezes last known data where no video link exists or scraping fails,
then writes india_yt_data.json back and updates delta_log.json.

To add new YouTube creators: update india_yt_data.json directly
(or extend the Apps Script, same as India Instagram).
"""

import json, re, os, sys, time, datetime, ssl, certifi, urllib.request, urllib.error

_SSL = ssl.create_default_context(cafile=certifi.where())

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
ACTOR_ID    = "bernardo~youtube-scraper"
MAX_WAIT_S  = 600
POLL_S      = 15
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
OUT_PATH    = os.path.join(SCRIPT_DIR, "india_yt_data.json")
DELTA_PATH  = os.path.join(SCRIPT_DIR, "delta_log.json")

VID_RE = re.compile(r'(?:watch\?v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})', re.I)

def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "python"})
    with urllib.request.urlopen(req, context=_SSL, timeout=30) as r:
        return r.read().decode('utf-8')

def fetch_json(url):
    return json.loads(fetch_text(url))

def post_json(url, payload):
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, context=_SSL, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        raise

# ══════════════════════════════════════════════════════════════════════════════
# Step 1: Load existing india_yt_data.json
# ══════════════════════════════════════════════════════════════════════════════
print("── Step 1: Loading india_yt_data.json ──")
with open(OUT_PATH) as f:
    data = json.load(f)
rows = data["rows"]
print(f"  {len(rows)} creators loaded")

# ══════════════════════════════════════════════════════════════════════════════
# Step 2: Collect video URLs that Apify can scrape
# ══════════════════════════════════════════════════════════════════════════════
def clean_vid_url(raw):
    return (raw or '').strip().split('?')[0].rstrip('/') if raw else ''

scrapeable = {}   # normalised_url → row index list
for i, r in enumerate(rows):
    vid = clean_vid_url(r.get('videoLink', ''))
    if vid and VID_RE.search(vid):
        scrapeable.setdefault(vid, []).append(i)

print(f"  {len(scrapeable)} unique video URLs to scrape")

# ══════════════════════════════════════════════════════════════════════════════
# Step 3: Apify YouTube scrape
# ══════════════════════════════════════════════════════════════════════════════
apify_results = {}   # normalised_url → {views, likes, comments, subscribers}

if scrapeable and APIFY_TOKEN:
    print("── Step 3: Apify YouTube scrape ──")
    urls = list(scrapeable.keys())
    run_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
    payload = {
        "startUrls":  [{"url": u} for u in urls],
        "maxResults": len(urls) * 2,
        "proxy":      {"useApifyProxy": True},
    }
    try:
        run_resp = post_json(run_url, payload)
        run_id   = run_resp["data"]["id"]
        ds_id    = run_resp["data"]["defaultDatasetId"]
        print(f"  Run ID: {run_id}")

        elapsed, status = 0, "RUNNING"
        while elapsed < MAX_WAIT_S:
            time.sleep(POLL_S); elapsed += POLL_S
            info   = fetch_json(f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}")
            status = info["data"]["status"]
            n      = info["data"].get("stats", {}).get("outputItems", "?")
            print(f"  [{elapsed}s] {status} — {n} items")
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break

        if status == "SUCCEEDED":
            items = fetch_json(
                f"https://api.apify.com/v2/datasets/{ds_id}/items"
                f"?token={APIFY_TOKEN}&format=json&limit=500"
            )
            print(f"  Got {len(items)} items from Apify")
            for it in items:
                raw_url = (it.get('url') or it.get('inputUrl') or '').split('?')[0].rstrip('/')
                if not raw_url: continue
                apify_results[raw_url] = {
                    'views':       it.get('viewCount') or it.get('views') or it.get('videoViewCount'),
                    'likes':       it.get('likeCount') or it.get('likes'),
                    'comments':    it.get('commentCount') or it.get('comments'),
                    'subscribers': it.get('channelSubscriberCount') or it.get('subscriberCount'),
                }
        else:
            print(f"  Scrape status: {status} — freezing all data", file=sys.stderr)

    except Exception as e:
        print(f"  Apify error: {e} — freezing all data", file=sys.stderr)

elif scrapeable and not APIFY_TOKEN:
    print("── Step 3: No APIFY_TOKEN — freezing all data ──")
else:
    print("── Step 3: No video URLs to scrape — freezing all data ──")

# ══════════════════════════════════════════════════════════════════════════════
# Step 4: Merge results; freeze where no match
# ══════════════════════════════════════════════════════════════════════════════
print("── Step 4: Merging ──")
updated = frozen = 0
for r in rows:
    vid  = clean_vid_url(r.get('videoLink', ''))
    hit  = apify_results.get(vid, {}) if vid else {}

    if hit:
        # Only overwrite if Apify returned a non-None value (don't zero-out on partial failures)
        if hit.get('views')   is not None: r['views']       = hit['views']
        if hit.get('likes')   is not None: r['likes']       = hit['likes']
        if hit.get('comments') is not None: r['comments']   = hit['comments']
        if hit.get('subscribers') is not None:
            r['subscribers'] = hit['subscribers']
            r['followers']   = hit['subscribers']
        r['refreshStatus'] = 'scraped'
        updated += 1
        print(f"  ✓ {r['name']:30s} views={r.get('views')}  subs={r.get('subscribers')}")
    else:
        r['refreshStatus'] = 'frozen'
        frozen += 1
        print(f"  ❄ {r['name']:30s} (frozen — {'no video link' if not vid else 'scrape miss'})")

print(f"\n  Updated: {updated}  Frozen: {frozen}")

# ══════════════════════════════════════════════════════════════════════════════
# Step 5: Write india_yt_data.json
# ══════════════════════════════════════════════════════════════════════════════
data['refreshedAt'] = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
data['rows'] = rows
with open(OUT_PATH, 'w') as f:
    json.dump(data, f, indent=2, default=str)
print(f"✅ Wrote {len(rows)} rows to india_yt_data.json")

# ══════════════════════════════════════════════════════════════════════════════
# Step 6: Update delta_log.json (IN_YT entry)
# ══════════════════════════════════════════════════════════════════════════════
try:
    with open(DELTA_PATH) as f:
        dl = json.load(f)
except FileNotFoundError:
    dl = {"logs": []}

today_iso = datetime.date.today().isoformat()
snap = {
    "creatorsLive": len([r for r in rows if (r.get('liveStatus') or '').lower() == 'live']),
    "totalViews":   sum(r.get('views') or 0 for r in rows),
    "totalLikes":   sum(r.get('likes') or 0 for r in rows),
    "totalComments":sum(r.get('comments') or 0 for r in rows),
    "totalCost":    sum(r.get('cost') or 0 for r in rows),
}

prev = next((e['IN_YT'] for e in reversed(dl.get('logs', []))
             if e.get('date') != today_iso and 'IN_YT' in e), None)

def delta(key):
    if prev is None: return None
    n, o = snap.get(key, 0) or 0, prev.get(key, 0) or 0
    return n - o

snap['delta'] = {k: delta(k) for k in snap}

today_entry = next((e for e in dl['logs'] if e.get('date') == today_iso), None)
if not today_entry:
    today_entry = {'date': today_iso}
    dl['logs'].append(today_entry)
today_entry['IN_YT'] = snap
dl['logs'] = sorted(dl['logs'], key=lambda e: e['date'])[-30:]

with open(DELTA_PATH, 'w') as f:
    json.dump(dl, f, indent=2)
print("✅ Updated delta_log.json (IN_YT)")
print(f"\nSummary: {len(rows)} creators | {snap['totalViews']:,} total views | ₹{snap['totalCost']:,} spend")
