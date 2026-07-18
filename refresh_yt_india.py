#!/usr/bin/env python3
"""
Refresh script for India YouTube influencer data.
Steps:
  1. Sync yt_sheet_data.json (pushed by Apps Script from the YouTube POA tab)
     into india_yt_data.json — picks up new creators and updated video links.
  2. Scrape fresh stats for every row that has a video link via Apify.
  3. Freeze last known data where no video link or scraping fails.
  4. Write india_yt_data.json + update delta_log.json.

Mirrors the exact pattern of refresh_metrics.py (India Instagram).
"""

import json, re, os, sys, time, datetime, ssl, certifi, urllib.request, urllib.error

_SSL = ssl.create_default_context(cafile=certifi.where())

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
ACTOR_ID    = "streamers~youtube-scraper"
MAX_WAIT_S  = 600
POLL_S      = 15
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
OUT_PATH    = os.path.join(SCRIPT_DIR, "india_yt_data.json")
YT_SHEET    = os.path.join(SCRIPT_DIR, "yt_sheet_data.json")
DELTA_PATH  = os.path.join(SCRIPT_DIR, "delta_log.json")

VID_RE   = re.compile(r'(?:watch\?v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})', re.I)
YT_RE    = re.compile(r'https?://(www\.)?youtube\.com/', re.I)
TIME_RE  = re.compile(r'^\d{1,2}:\d{2}$')
MO = ['January','February','March','April','May','June',
      'July','August','September','October','November','December']

def month_order(s):
    s = (s or '').strip()
    for i, m in enumerate(MO):
        if m.lower() in s.lower():
            y = re.search(r'(\d{4})', s)
            return int(str(y.group(1)) + f'{i+1:02d}') if y else 0
    return 0

def region_to_lang(region):
    r = (region or '').lower()
    if 'malay' in r or 'kerala' in r: return 'Malayalam'
    if 'kannada' in r or 'bangalore' in r or 'bengaluru' in r: return 'Kannada'
    if 'tamil' in r or 'chennai' in r: return 'Tamil'
    if 'telugu' in r or 'hyderabad' in r or 'andhra' in r: return 'Telugu'
    return 'Hindi'

def num(s):
    if not s: return None
    v = re.sub(r'[^0-9.]', '', str(s))
    try: return float(v) if '.' in v else int(v)
    except: return None

def clean_yt_url(raw):
    for tok in re.split(r'[\s\n,]+', (raw or '')):
        tok = tok.strip()
        if YT_RE.match(tok) or 'youtu.be' in tok:
            # Extract video ID and return canonical form to avoid matching bugs
            m = VID_RE.search(tok)
            if m:
                return f'https://www.youtube.com/watch?v={m.group(1)}'
            return tok.split('?')[0].rstrip('/')
    return ''

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
# Step 1: Load existing india_yt_data.json for freeze fallback
# ══════════════════════════════════════════════════════════════════════════════
print("── Step 1: Loading existing india_yt_data.json ──")
existing_by_name = {}
try:
    with open(OUT_PATH) as f:
        old = json.load(f)
    for r in old.get('rows', []):
        existing_by_name[r.get('name','').strip().lower()] = r
    print(f"  {len(existing_by_name)} existing rows (freeze fallback)")
except FileNotFoundError:
    print("  No existing file — starting fresh")

# ══════════════════════════════════════════════════════════════════════════════
# Step 2: Sync from yt_sheet_data.json (Apps Script export of YouTube POA tab)
# ══════════════════════════════════════════════════════════════════════════════
print("── Step 2: Syncing from yt_sheet_data.json ──")
rows = []
if not os.path.exists(YT_SHEET):
    print("  yt_sheet_data.json not found — using existing rows only")
    rows = list(existing_by_name.values())
else:
    try:
        with open(YT_SHEET) as f:
            yt_sheet = json.load(f)
        sheet_rows = yt_sheet.get('rows', [])
        print(f"  {len(sheet_rows)} rows in yt_sheet_data.json")

        for i, sr in enumerate(sheet_rows):
            name = (sr.get('creator_name') or sr.get('name') or '').strip()
            if not name: continue

            # Determine if executed (Final Cost > 0 OR Watch Time in M:SS format)
            cost_raw = sr.get('final_cost') or sr.get('cost') or ''
            wt       = (sr.get('avg_watch_time') or sr.get('watch_time') or sr.get('watchtime') or '').strip()
            cost_val = num(cost_raw)
            is_exec  = (cost_val and cost_val > 0) or bool(TIME_RE.match(wt))
            if not is_exec:
                continue

            channel_link = clean_yt_url(sr.get('link') or sr.get('channel_link') or sr.get('channel_url') or sr.get('channel') or '')
            video_link   = clean_yt_url(sr.get('video_live_link') or sr.get('video_link') or sr.get('video_url') or sr.get('reel_link') or '')
            raw_month    = (sr.get('live_month') or sr.get('month') or '').strip()
            # Convert "2026-01-01" date strings → "January 2026"
            live_month   = raw_month
            import re as _re2
            _dm = _re2.match(r'(\d{4})-(\d{2})-\d{2}', raw_month)
            if _dm:
                live_month = f"{MO[int(_dm.group(2))-1]} {_dm.group(1)}"

            # Merge with existing row for freeze fields
            old_r = existing_by_name.get(name.lower(), {})

            row = {
                'id':          i,
                'name':        name,
                'channelLink': channel_link,
                'videoLink':   video_link,
                'link':        video_link or channel_link,
                'subscribers': num(sr.get('subscribers') or sr.get('subs') or old_r.get('subscribers')),
                'followers':   num(sr.get('subscribers') or sr.get('subs') or old_r.get('subscribers')),
                'views':       num(sr.get('views_on_30_jun') or sr.get('avg_views') or sr.get('views') or old_r.get('views')),
                'plannedViews':num(sr.get('avg_views') or old_r.get('plannedViews')),
                'likes':       num(sr.get('likes') or old_r.get('likes')),
                'comments':    num(sr.get('comments') or old_r.get('comments')),
                'cost':        cost_val,
                'cpv':         num(sr.get('actual_cpv_as_on_30062026') or sr.get('avg_cpv') or sr.get('cpv') or old_r.get('cpv')),
                'avgWatchTime':wt,
                'agency':      (sr.get('agency') or sr.get('partner') or old_r.get('agency') or 'Direct').strip(),
                'liveStatus':  (sr.get('status') or sr.get('live_status') or old_r.get('liveStatus') or 'Live').strip(),
                'liveMonth':   live_month or old_r.get('liveMonth', ''),
                'monthOrder':  month_order(live_month) if live_month else old_r.get('monthOrder', 0),
                'region':      (sr.get('city') or sr.get('region') or sr.get('language') or old_r.get('region') or 'PAN INDIA').strip(),
                'language':    region_to_lang(sr.get('city') or sr.get('region') or sr.get('language') or old_r.get('region', '')),
                'platform':    'YouTube',
                'refreshStatus': old_r.get('refreshStatus', 'frozen'),
            }
            rows.append(row)

        print(f"  {len(rows)} executed creators after filter")
    except Exception as e:
        print(f"  Error reading yt_sheet_data.json: {e} — using existing rows", file=sys.stderr)
        rows = list(existing_by_name.values())

if not rows:
    rows = list(existing_by_name.values())
    print(f"  Falling back to {len(rows)} existing rows")

# ══════════════════════════════════════════════════════════════════════════════
# Step 2: Collect video URLs that Apify can scrape
# ══════════════════════════════════════════════════════════════════════════════
def vid_id(raw):
    m = VID_RE.search(raw or '')
    return m.group(1) if m else ''

scrapeable = {}   # canonical_watch_url → row index list
for i, r in enumerate(rows):
    vid = vid_id(r.get('videoLink', ''))
    if vid:
        canon = f'https://www.youtube.com/watch?v={vid}'
        scrapeable.setdefault(canon, []).append(i)

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
                raw_url = it.get('url') or it.get('inputUrl') or ''
                vid = vid_id(raw_url)
                key = f'https://www.youtube.com/watch?v={vid}' if vid else raw_url.split('?')[0].rstrip('/')
                if not key: continue
                apify_results[key] = {
                    'views':       it.get('viewCount') or it.get('videoViewCount') or it.get('views'),
                    'likes':       it.get('likes') or it.get('likeCount'),
                    'comments':    it.get('commentsCount') or it.get('commentCount') or it.get('comments'),
                    'subscribers': it.get('numberOfSubscribers') or it.get('channelSubscriberCount') or it.get('subscriberCount'),
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
    _v   = vid_id(r.get('videoLink', ''))
    vid  = f'https://www.youtube.com/watch?v={_v}' if _v else ''
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
data = {}
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
