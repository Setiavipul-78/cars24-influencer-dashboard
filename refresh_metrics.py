#!/usr/bin/env python3
"""
Refresh script — two steps on every run:
  1. Sync new rows from Google Sheet → live_data.json (auto-picks up new influencers)
  2. Trigger fresh Apify Instagram scrape for all non-csvPin video links
csvPin=true rows are never overwritten (manually verified metrics from CSV).
"""

import csv, io, json, re, urllib.request, urllib.error, datetime, os, sys, time

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "apify_api_U2idmzmhBnzlnMBi71su8BR6EzzVE30NEfF4")
ACTOR_ID    = "apify~instagram-scraper"
BATCH_SIZE  = 50
MAX_WAIT_S  = 600
POLL_S      = 10

# Google Sheet (must be shared as "Anyone with link can view")
SHEET_ID  = "1VBjkdMm5Uhjq-JLmGqRL7aFTbTH3P0c7oRr1LHcN1o0"
SHEET_GID = "1928933144"
SHEET_CSV = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"

SC_RE    = re.compile(r'/(?:reel|reels|p)/([A-Za-z0-9_-]+)(?:/|\?|$)')
INSTA_RE = re.compile(r'^https?://(www\.)?instagram\.com/', re.I)
MO       = ['January','February','March','April','May','June',
            'July','August','September','October','November','December']

# ── Category classification (mirrors frontend logic) ──────────────────────────
MANUAL_CAT = {
    'Cars with rohit':'Automobile','carki kaksha':'Automobile','fuelburner2.0':'Automobile',
    'thedriverseatguy':'Automobile','neffzcapture':'Automobile','AutoFact Bharat':'Automobile',
    'The auto Bharat':'Automobile','Carversal':'Automobile','masterwheel1':'Automobile',
    'indiandriveguide':'Automobile','the carsio':'Automobile','priyanshgarage':'Automobile',
    'arunesh_a2y':'Automobile','Tr engine':'Automobile','kowshik_maridi':'Automobile',
    'rajeevfinance':'Finance','prettymuchbusiness':'Finance','prettymuchfinance':'Finance',
    'financewithmadhav':'Finance','income_tax_rules':'Finance','ca_sharathjyothsna':'Finance',
    'Nitin Soni':'Finance','Krish  Mehta':'Finance','sauravguptax':'Finance',
    'chillarboys':'Entertainment','Aakhri Pasta':'Entertainment','Jomedy':'Entertainment',
    'theprooshow':'Entertainment','abrardoingthings':'Entertainment','mooookesh':'Entertainment',
    'Zee Aly':'Entertainment','bun_gulkan':'Entertainment','chillpatill':'Entertainment',
    'Gawathi':'Entertainment','Sahil Beniwal':'Entertainment',
    'vasundhramanhas_vlogs':'Vlogs','Hyderabad Diaries':'Vlogs','Smruti & Onkar':'Vlogs',
    'jogipet_ratnam':'Vlogs','iammharshvlogs':'Vlogs','Bihariladka':'Vlogs',
    'pavandeee':'Vlogs','Awadhesh Kumar':'Vlogs','Gagan':'Vlogs','Yaarivanu':'Vlogs',
    'Manik.ai':'Infotainment','CactussAI':'Infotainment','AI Food Story':'Infotainment',
    'Bangalore trends':'Infotainment','Gramin Kids':'Infotainment','Dr. Nehal Pasha':'Infotainment',
    'Mehfil - e - mishra':'Infotainment','Tanishk aladkat':'Infotainment',
    'Devashish Gaur':'Infotainment','City Fillagallu':'Infotainment','Dil se paneer':'Infotainment',
}
AUTO_KW  = ['car','auto','garage','wheel','drive','motor','fuel','engine','gear','carsio','carversal','carki','theauto','autofact','neffz','masterwheel','indiandrive','priyanshgarage']
FIN_KW   = ['finance','money','invest','tax','ca_sha','wealth','stock','income_tax','prettymuchbusiness','prettymuchfinance','rajeevfinance']
ENT_KW   = ['comedy','jomedy','pasta','chillar','prooshow','mooookesh','zee aly','bun_gulkan','chillpatill','abrar','gawathi']
VLOG_KW  = ['vlog','diary','diaries','smruti','jogipet','iammharsh','bihariladka','pavandeee','vasundh','yaarivanu']
INFO_KW  = ['.ai','manik','cactuss','ai food','bangalore','gramin','nehal','mehfil','city fillagallu','tanishk','devashish']

def classify(name):
    if name in MANUAL_CAT: return MANUAL_CAT[name]
    n = name.lower()
    if any(k in n for k in AUTO_KW):  return 'Automobile'
    if any(k in n for k in FIN_KW):   return 'Finance'
    if any(k in n for k in ENT_KW):   return 'Entertainment'
    if any(k in n for k in VLOG_KW):  return 'Vlogs'
    if any(k in n for k in INFO_KW):  return 'Infotainment'
    return 'Entertainment'  # default

def shortcode(url):
    m = SC_RE.search(url or '')
    return m.group(1) if m else None

def clean_insta_url(raw):
    candidates = [l.strip() for l in raw.replace('\n',' ').split() if INSTA_RE.match(l.strip())]
    return (candidates[0].split('?')[0].rstrip('/') + '/') if candidates else ''

def num(s):
    if not s: return None
    v = re.sub(r'[^0-9.]', '', str(s))
    try: return float(v) if '.' in v else int(v)
    except: return None

def month_order(s):
    for i, m in enumerate(MO):
        if m.lower() in s.lower():
            y = re.search(r'(\d{4})', s)
            return int(str(y.group(1)) + f'{i+1:02d}') if y else 0
    return 0

def tier_of(f):
    if not f: return 'Unknown'
    if f < 10000:  return 'Nano'
    if f < 50000:  return 'Micro'
    if f < 500000: return 'Macro'
    return 'Mega'

def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "python"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8')

def fetch_json(url):
    return json.loads(fetch_text(url))

def post_json(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        raise

# ══════════════════════════════════════════════════════════════════════════════
#  STEP 1 — Sync Google Sheet → live_data.json
# ══════════════════════════════════════════════════════════════════════════════
print("── Step 1: Syncing Google Sheet ──")
try:
    csv_text = fetch_text(SHEET_CSV)
    reader   = list(csv.reader(io.StringIO(csv_text)))
except Exception as e:
    print(f"Could not fetch sheet: {e} — skipping sync", file=sys.stderr)
    reader = []

script_dir = os.path.dirname(os.path.abspath(__file__))
live_path  = os.path.join(script_dir, "live_data.json")

with open(live_path) as f:
    data = json.load(f)

rows = data["rows"]

if reader and len(reader) > 1:
    hdrs = [h.lower().replace(' ','_').replace('/','_') for h in reader[0]]

    def col(name):
        for i, h in enumerate(hdrs):
            if h == name: return i
        for i, h in enumerate(hdrs):
            if name in h: return i
        return -1

    C = {
        'month':      col('live_month'),
        'agency':     col('agency'),
        'name':       col('name') if col('name') >= 0 else col('influencer_name'),
        'link':       col('link'),
        'region':     col('region'),
        'followers':  col('follower'),
        'avg_views':  col('avg_view'),
        'cost':       col('final_cost'),
        'status':     col('live_status'),
        'business':   col('business'),
        'video_link': col('video_live_link'),
        'views':      col('views'),
        'likes':      col('likes'),
        'comments':   col('comments'),
        'shares':     col('shares'),
        'saves':      col('saves'),
    }

    # Build lookup key → row index in live_data
    existing = {}
    for i, r in enumerate(rows):
        key = (r['name'].strip().lower(), r['liveMonth'].strip().lower())
        existing[key] = i

    added = 0
    for sheet_row in reader[1:]:
        if len(sheet_row) < 3: continue
        name = sheet_row[C['name']].strip() if C['name'] >= 0 and C['name'] < len(sheet_row) else ''
        if not name: continue
        month = sheet_row[C['month']].strip() if C['month'] >= 0 and C['month'] < len(sheet_row) else ''
        key   = (name.lower(), month.lower())

        video_link_raw = sheet_row[C['video_link']].strip() if C['video_link'] >= 0 and C['video_link'] < len(sheet_row) else ''
        video_link     = clean_insta_url(video_link_raw) or video_link_raw

        agency    = sheet_row[C['agency']].strip()    if C['agency']    >= 0 and C['agency']    < len(sheet_row) else ''
        region    = sheet_row[C['region']].strip()    if C['region']    >= 0 and C['region']    < len(sheet_row) else 'PAN INDIA'
        followers = num(sheet_row[C['followers']])    if C['followers'] >= 0 and C['followers'] < len(sheet_row) else None
        cost      = num(sheet_row[C['cost']])         if C['cost']      >= 0 and C['cost']      < len(sheet_row) else None
        status    = sheet_row[C['status']].strip()    if C['status']    >= 0 and C['status']    < len(sheet_row) else ''
        business  = sheet_row[C['business']].strip()  if C['business']  >= 0 and C['business']  < len(sheet_row) else ''
        link      = sheet_row[C['link']].strip()      if C['link']      >= 0 and C['link']      < len(sheet_row) else ''
        avg_views = num(sheet_row[C['avg_views']])    if C['avg_views'] >= 0 and C['avg_views'] < len(sheet_row) else None

        if key in existing:
            # Update metadata fields only — never touch metrics
            r = rows[existing[key]]
            r['agency']    = agency    or r.get('agency', '')
            r['region']    = region    or r.get('region', 'PAN INDIA')
            r['cost']      = cost      if cost is not None else r.get('cost')
            r['liveStatus']= status    or r.get('liveStatus', '')
            r['business']  = business  or r.get('business', '')
            r['videoLink'] = video_link or r.get('videoLink', '')
            r['link']      = link      or r.get('link', '')
            r['followers'] = followers if followers is not None else r.get('followers')
            r['avgViews']  = avg_views if avg_views is not None else r.get('avgViews')
            r['tier']      = tier_of(r['followers'])
        else:
            # New row — add it
            mo = month_order(month)
            new_row = {
                'id':           max((r['id'] for r in rows), default=0) + 1,
                'name':         name,
                'agency':       agency,
                'region':       region or 'PAN INDIA',
                'liveMonth':    month,
                'monthOrder':   mo,
                'liveStatus':   status,
                'business':     business,
                'link':         link,
                'videoLink':    video_link,
                'followers':    followers,
                'avgViews':     avg_views,
                'cost':         cost,
                'tier':         tier_of(followers),
                'views':        None,
                'likes':        None,
                'comments':     None,
                'shares':       None,
                'saves':        None,
                'engRate':      None,
                'cpv':          None,
                'refreshStatus':'pending',
                'lastRefreshed':None,
                'category':     classify(name),
            }
            rows.append(new_row)
            existing[key] = len(rows) - 1
            added += 1
            print(f"  + New row: {name} ({month})")

    print(f"Sheet sync: {added} new rows added, {len(rows)-added} existing updated")
else:
    print("  Sheet empty or unavailable — skipping sync")

# ══════════════════════════════════════════════════════════════════════════════
#  STEP 2 — Fresh Apify scrape
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Step 2: Apify scrape ──")
urls_to_scrape = []
for r in rows:
    if r.get("csvPin"): continue
    link = clean_insta_url(r.get("videoLink", ""))
    if shortcode(link):
        urls_to_scrape.append(link)

urls_to_scrape = list(dict.fromkeys(urls_to_scrape))
print(f"URLs to scrape: {len(urls_to_scrape)}")

apify   = {}
run_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
batches = [urls_to_scrape[i:i+BATCH_SIZE] for i in range(0, len(urls_to_scrape), BATCH_SIZE)]

for b_idx, batch in enumerate(batches):
    print(f"\nBatch {b_idx+1}/{len(batches)} — {len(batch)} URLs")
    run_resp = post_json(run_url, {"directUrls": batch, "resultsType": "posts", "resultsLimit": 1})
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
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"): break

    if status != "SUCCEEDED":
        print(f"  Batch {b_idx+1} failed ({status}) — skipping", file=sys.stderr)
        continue

    items = fetch_json(f"https://api.apify.com/v2/datasets/{ds_id}/items?token={APIFY_TOKEN}&format=json&limit=200")
    print(f"  Got {len(items)} items")
    for it in items:
        raw  = it.get("url") or it.get("inputUrl") or ""
        sc   = shortcode(raw) or it.get("shortCode") or it.get("shortcode") or ""
        if not sc: continue
        lk   = it.get("likesCount")
        apify[sc] = {
            "views":    it.get("videoPlayCount") or 0,
            "likes":    None if lk is None or lk < 0 else lk,
            "comments": it.get("commentsCount", 0) or 0,
            "ts":       it.get("timestamp", ""),
        }

print(f"\nTotal shortcodes from Apify: {len(apify)}")

# ── Apply Apify results to rows ───────────────────────────────────────────────
updated = skipped_pin = no_match = 0
for r in rows:
    if r.get("csvPin"):
        skipped_pin += 1; continue
    sc = shortcode(r.get("videoLink", ""))
    if not sc or sc not in apify:
        no_match += 1; continue

    m = apify[sc]
    new_views    = m["views"]    if m["views"]             else r.get("views")
    new_likes    = m["likes"]    if m["likes"] is not None  else r.get("likes")
    new_comments = m["comments"] if m["comments"]           else r.get("comments")
    r.update({
        "views": new_views, "likes": new_likes, "comments": new_comments,
        "lastRefreshed": m["ts"] or datetime.datetime.utcnow().isoformat() + "Z",
        "refreshStatus": "ok",
    })
    shares = r.get("shares") or 0
    saves  = r.get("saves")  or 0
    eng    = (new_likes or 0) + shares + (new_comments or 0) + saves
    r["engRate"] = round(eng / new_views * 100, 2) if new_views else None
    r["cpv"]     = round(r["cost"] / new_views, 3)  if (r.get("cost") and new_views) else None
    updated += 1

# ── Save ──────────────────────────────────────────────────────────────────────
data["refreshedAt"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
data["rows"]        = rows

with open(live_path, "w") as f:
    json.dump(data, f, indent=2)

print(f"\nDone — updated {updated} rows, {skipped_pin} csvPin skipped, {no_match} no Apify match")
print(f"Wrote {live_path}")
