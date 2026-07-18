#!/usr/bin/env python3
"""
Refresh script for India YouTube influencer data.
Steps:
  1. Fetch "pan india poa youtube" tab from Google Sheet (CSV export)
  2. Filter to executed creators (Final Cost > 0 OR Watch Time in M:SS format)
  3. For each creator with a Video Link → scrape live stats via Apify YouTube scraper
  4. For creators without a Video Link or where scraping fails → freeze last known data
  5. Write india_yt_data.json and update delta_log.json (IN_YT entry)
"""

import json, re, csv, io, os, sys, time, datetime, ssl, certifi, urllib.request, urllib.error

_SSL = ssl.create_default_context(cafile=certifi.where())

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
SHEET_ID    = "1VBjkdMm5Uhjq-JLmGqRL7aFTbTH3P0c7oRr1LHcN1o0"
GID         = "1490331911"          # pan india poa youtube tab
ACTOR_ID    = "bernardo~youtube-scraper"
MAX_WAIT_S  = 600
POLL_S      = 15
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
OUT_PATH    = os.path.join(SCRIPT_DIR, "india_yt_data.json")
DELTA_PATH  = os.path.join(SCRIPT_DIR, "delta_log.json")

YT_RE  = re.compile(r'https?://(www\.)?youtube\.com/', re.I)
VID_RE = re.compile(r'(?:watch\?v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})', re.I)
TIME_RE = re.compile(r'^\d{1,2}:\d{2}$')   # M:SS or MM:SS

MO = ['January','February','March','April','May','June',
      'July','August','September','October','November','December']

def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "python"})
    with urllib.request.urlopen(req, context=_SSL, timeout=30) as r:
        return r.read().decode('utf-8')

def fetch_json(url):
    return json.loads(fetch_text(url))

def post_json(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, context=_SSL, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        raise

def num(s):
    if not s: return None
    v = re.sub(r'[^0-9.]', '', str(s))
    try: return float(v) if '.' in v else int(v)
    except: return None

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

def is_executed(row_cells, col):
    """Row is executed if Final Cost > 0 OR Watch Time looks like M:SS."""
    cost = num(row_cells.get(col['final_cost'], ''))
    wt   = (row_cells.get(col['watch_time'], '') or '').strip()
    return (cost and cost > 0) or bool(TIME_RE.match(wt))

def clean_yt_url(raw):
    for token in re.split(r'[\s\n]+', (raw or '')):
        token = token.strip()
        if YT_RE.match(token):
            return token.split('?')[0] if '&' not in token else token
    return ''

# ══════════════════════════════════════════════════════════════════════════════
# Step 1: Fetch YouTube POA sheet
# ══════════════════════════════════════════════════════════════════════════════
print("── Step 1: Fetching YouTube POA sheet ──")
url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"
try:
    raw_csv = fetch_text(url)
except urllib.error.HTTPError as e:
    if e.code == 401:
        print("⚠️  Sheet returned 401 — not shared publicly.", file=sys.stderr)
        print("   Go to Google Sheets → File → Share → Publish to web (CSV) to fix this.", file=sys.stderr)
        print("   Falling back to existing india_yt_data.json with no updates.", file=sys.stderr)
        # Re-write the existing file untouched so the workflow still commits cleanly
        if os.path.exists(OUT_PATH):
            sys.exit(0)
        sys.exit(1)
    print(f"Failed to fetch sheet: {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"Failed to fetch sheet: {e}", file=sys.stderr)
    sys.exit(1)

reader = csv.reader(io.StringIO(raw_csv))
all_rows = list(reader)
if not all_rows:
    print("Sheet is empty", file=sys.stderr); sys.exit(1)

# Detect header row (first row with 'Creator Name' or 'Name')
header_idx = 0
for i, row in enumerate(all_rows[:5]):
    joined = ' '.join(row).lower()
    if 'creator' in joined or 'name' in joined:
        header_idx = i; break

header = [h.strip().lower() for h in all_rows[header_idx]]
print(f"Header ({len(header)} cols): {header}")

def ci(keywords):
    """Return index of first column matching any keyword."""
    for kw in keywords:
        for i, h in enumerate(header):
            if kw in h: return i
    return -1

col = {
    'name':        ci(['creator name', 'name']),
    'channel':     ci(['channel link', 'channel url', 'channel']),
    'video':       ci(['video link', 'video url', 'reel link', 'post link']),
    'subscribers': ci(['subscriber', 'subs']),
    'city':        ci(['city', 'location']),
    'views':       ci(['avg view', 'views']),
    'likes':       ci(['likes']),
    'watch_time':  ci(['watch time', 'watchtime']),
    'final_cost':  ci(['final cost', 'cost', 'budget']),
    'cpv':         ci(['cpv']),
    'agency':      ci(['agency', 'partner']),
    'live_status': ci(['live status', 'status']),
    'live_month':  ci(['live month', 'month']),
    'region':      ci(['region', 'language', 'lang']),
    'platform':    ci(['platform']),
}
print(f"Column map: { {k:v for k,v in col.items() if v >= 0} }")

def cell(row, key):
    idx = col.get(key, -1)
    if idx < 0 or idx >= len(row): return ''
    return row[idx].strip()

data_rows = all_rows[header_idx + 1:]
print(f"Total data rows: {len(data_rows)}")

# ══════════════════════════════════════════════════════════════════════════════
# Step 2: Filter executed creators
# ══════════════════════════════════════════════════════════════════════════════
print("── Step 2: Filtering executed creators ──")
executed = []
for row in data_rows:
    if not any(row): continue
    name = cell(row, 'name')
    if not name: continue

    final_cost = num(cell(row, 'final_cost'))
    wt = cell(row, 'watch_time')
    is_exec = (final_cost and final_cost > 0) or bool(TIME_RE.match(wt))
    if not is_exec: continue

    channel_link = clean_yt_url(cell(row, 'channel'))
    video_link   = clean_yt_url(cell(row, 'video'))
    subs         = num(cell(row, 'subscribers'))
    views_sheet  = num(cell(row, 'views'))
    likes_sheet  = num(cell(row, 'likes'))
    cost         = final_cost
    cpv          = num(cell(row, 'cpv'))
    agency       = cell(row, 'agency') or 'Direct'
    status       = cell(row, 'live_status') or 'Live'
    live_month   = cell(row, 'live_month') or ''
    region       = cell(row, 'region') or 'PAN INDIA'
    platform     = cell(row, 'platform') or 'YouTube'

    executed.append({
        'name':         name,
        'channelLink':  channel_link,
        'videoLink':    video_link,
        'subscribers':  subs,
        'views':        views_sheet,
        'likes':        likes_sheet,
        'cost':         cost,
        'cpv':          cpv,
        'agency':       agency,
        'liveStatus':   status,
        'liveMonth':    live_month,
        'monthOrder':   month_order(live_month),
        'region':       region,
        'language':     region_to_lang(region),
        'platform':     platform,
        '_scrapeUrl':   video_link or channel_link,  # what to send to Apify
    })

print(f"Executed creators: {len(executed)}")

# ══════════════════════════════════════════════════════════════════════════════
# Step 3: Load existing data for freeze-on-failure
# ══════════════════════════════════════════════════════════════════════════════
existing = {}
try:
    with open(OUT_PATH) as f:
        old = json.load(f)
    for r in old.get('rows', []):
        existing[r.get('name', '').strip().lower()] = r
    print(f"Loaded {len(existing)} existing rows for freeze fallback")
except FileNotFoundError:
    print("No existing india_yt_data.json — starting fresh")

# ══════════════════════════════════════════════════════════════════════════════
# Step 4: Apify YouTube scrape (only creators with a video link)
# ══════════════════════════════════════════════════════════════════════════════
apify_results = {}  # url → {views, likes, comments, subscribers}

to_scrape = [r['videoLink'] for r in executed if r.get('videoLink') and VID_RE.search(r['videoLink'])]
print(f"── Step 4: Scraping {len(to_scrape)} video URLs via Apify ──")

if to_scrape and APIFY_TOKEN:
    run_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
    # Batch all into one run
    payload = {
        "startUrls":  [{"url": u} for u in to_scrape],
        "maxResults": len(to_scrape) * 2,
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
                input_url = (it.get('url') or it.get('inputUrl') or '').split('?')[0].rstrip('/')
                if not input_url: continue
                apify_results[input_url] = {
                    'views':       it.get('viewCount') or it.get('views') or it.get('videoViewCount'),
                    'likes':       it.get('likeCount') or it.get('likes'),
                    'comments':    it.get('commentCount') or it.get('comments'),
                    'subscribers': it.get('channelSubscriberCount') or it.get('subscriberCount'),
                }
        else:
            print(f"  Scrape failed ({status}) — will use frozen data", file=sys.stderr)
    except Exception as e:
        print(f"  Apify error: {e} — will use frozen data", file=sys.stderr)
elif to_scrape and not APIFY_TOKEN:
    print("  No APIFY_TOKEN — skipping scrape, using frozen data")

# ══════════════════════════════════════════════════════════════════════════════
# Step 5: Merge scrape results with sheet data; freeze where needed
# ══════════════════════════════════════════════════════════════════════════════
print("── Step 5: Merging results ──")

def freeze(creator, key):
    """Return last known value from existing data."""
    old = existing.get(creator['name'].strip().lower(), {})
    return old.get(key)

final_rows = []
for i, cr in enumerate(executed):
    vid_url = (cr.get('videoLink') or '').split('?')[0].rstrip('/')
    apify   = apify_results.get(vid_url, {})

    views    = apify.get('views')    if apify else None
    likes    = apify.get('likes')    if apify else None
    comments = apify.get('comments') if apify else None
    subs_api = apify.get('subscribers') if apify else None

    # Freeze fallback: use last known data if live scrape didn't return
    if views is None:    views    = cr.get('views') or freeze(cr, 'views')
    if likes is None:    likes    = cr.get('likes') or freeze(cr, 'likes')
    if comments is None: comments = freeze(cr, 'comments')
    if subs_api is None: subs_api = cr.get('subscribers') or freeze(cr, 'subscribers')

    scraped = bool(apify)
    row = {
        'id':           i,
        'name':         cr['name'],
        'channelLink':  cr['channelLink'],
        'videoLink':    cr['videoLink'],
        'link':         cr['videoLink'] or cr['channelLink'],
        'subscribers':  subs_api,
        'followers':    subs_api,          # alias for dashboard compat
        'views':        views,
        'likes':        likes,
        'comments':     comments,
        'cost':         cr['cost'],
        'cpv':          cr['cpv'],
        'agency':       cr['agency'],
        'liveStatus':   cr['liveStatus'],
        'liveMonth':    cr['liveMonth'],
        'monthOrder':   cr['monthOrder'],
        'region':       cr['region'],
        'language':     cr['language'],
        'platform':     cr['platform'],
        'refreshStatus': 'scraped' if scraped else 'frozen',
    }
    final_rows.append(row)
    flag = '✓' if scraped else '❄'
    print(f"  {flag} {cr['name']:30s} views={views}  subs={subs_api}")

# ══════════════════════════════════════════════════════════════════════════════
# Step 6: Write india_yt_data.json
# ══════════════════════════════════════════════════════════════════════════════
now_iso = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
output = {
    "refreshedAt": now_iso,
    "platform":    "YouTube",
    "rows":        final_rows,
}
with open(OUT_PATH, 'w') as f:
    json.dump(output, f, indent=2, default=str)
print(f"\n✅ Wrote {len(final_rows)} rows to {OUT_PATH}")

# ══════════════════════════════════════════════════════════════════════════════
# Step 7: Update delta_log.json with IN_YT entry
# ══════════════════════════════════════════════════════════════════════════════
try:
    with open(DELTA_PATH) as f:
        dl = json.load(f)
except FileNotFoundError:
    dl = {"logs": []}

today_iso = datetime.date.today().isoformat()
live_rows = [r for r in final_rows if (r.get('liveStatus') or '').lower() == 'live']
snap = {
    "creatorsLive": len(live_rows),
    "totalViews":   sum(r.get('views') or 0 for r in final_rows),
    "totalLikes":   sum(r.get('likes') or 0 for r in final_rows),
    "totalComments":sum(r.get('comments') or 0 for r in final_rows),
    "totalCost":    sum(r.get('cost') or 0 for r in final_rows),
}

# Find previous IN_YT entry for delta
prev_yt = None
for entry in reversed(dl.get('logs', [])):
    if entry.get('date') != today_iso and 'IN_YT' in entry:
        prev_yt = entry['IN_YT']
        break

def d(new, old, key):
    if old is None: return None
    n, o = new.get(key, 0), old.get(key, 0)
    return (n - o) if (n is not None and o is not None) else None

yt_delta = {k: d(snap, prev_yt, k) for k in snap} if prev_yt else {k: None for k in snap}
snap['delta'] = yt_delta

# Upsert today's log entry
today_entry = next((e for e in dl['logs'] if e.get('date') == today_iso), None)
if not today_entry:
    today_entry = {'date': today_iso}
    dl['logs'].append(today_entry)
today_entry['IN_YT'] = snap
dl['logs'] = sorted(dl['logs'], key=lambda e: e['date'])[-30:]

with open(DELTA_PATH, 'w') as f:
    json.dump(dl, f, indent=2)
print(f"✅ Updated delta_log.json (IN_YT)")
print(f"\nSummary: {len(final_rows)} creators | {snap['totalViews']:,} views | ₹{snap['totalCost']:,} spend")
