#!/usr/bin/env python3
"""
Auto-refresh script for GitHub Actions.
Fetches fresh metrics from all Apify datasets and updates live_data.json in-place.
Run this on a schedule — no CSV or all_rows.json needed.
"""

import json, re, urllib.request, datetime, os, sys

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "apify_api_U2idmzmhBnzlnMBi71su8BR6EzzVE30NEfF4")

DATASET_IDS = [
    "xnLil4aUJXXbPCj5E", "Cx9TfOfFfJdO61ub9", "nXQddm8XWjKai5eUC", "zjkRqVJfahgO1OPvb",
    "EvtSrIhY9FtUHPQuV", "YkmoHg3yEpu5tq67J", "aMlYWPnUa41cvc2aE", "3XAew0JVQSMtAtAE8",
    "clnxRLqb5oVJOq0yi", "TsHywdFaKOoFgnFtg", "JYRs6JQIPjjm5gAQ6",
    "MokbbJqMgCNucfOg9", "5H3henkgm9sP6oyPM", "4VgobhiAud5gHa8oL",
    "rksOMGctfwd9MptTj", "4BmYg60yT0dRHkSWc", "ro3enITf98olW6huB",
]

SC_RE = re.compile(r'/(?:reel|reels|p)/([A-Za-z0-9_-]+)(?:/|\?|$)')

def shortcode(url):
    m = SC_RE.search(url or '')
    return m.group(1) if m else None

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "python"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

print("Fetching Apify datasets…")
apify = {}
for ds_id in DATASET_IDS:
    url = f"https://api.apify.com/v2/datasets/{ds_id}/items?token={APIFY_TOKEN}&format=json&limit=100"
    try:
        items = fetch_json(url)
        for it in items:
            raw = it.get('url') or it.get('inputUrl') or ''
            sc = shortcode(raw) or it.get('shortCode') or it.get('shortcode') or ''
            if not sc:
                continue
            views = it.get('videoPlayCount') or 0
            lk = it.get('likesCount')
            lk = None if lk is None or lk < 0 else lk
            apify[sc] = {
                'views':    views,
                'likes':    lk,
                'comments': it.get('commentsCount', 0) or 0,
                'ts':       it.get('timestamp', ''),
            }
        print(f"  {ds_id}: {len(items)} items")
    except Exception as e:
        print(f"  ERROR {ds_id}: {e}", file=sys.stderr)

print(f"Loaded {len(apify)} unique shortcodes from Apify")

# Load current live_data.json
script_dir = os.path.dirname(os.path.abspath(__file__))
live_path  = os.path.join(script_dir, 'live_data.json')

with open(live_path) as f:
    data = json.load(f)

rows = data['rows']
updated = 0
skipped = 0

for r in rows:
    sc = shortcode(r.get('videoLink', ''))
    if not sc or sc not in apify:
        skipped += 1
        continue

    m = apify[sc]
    new_views    = m['views'] if m['views'] else r.get('views')
    new_likes    = m['likes'] if m['likes'] is not None else r.get('likes')
    new_comments = m['comments'] if m['comments'] else r.get('comments')

    if new_views != r.get('views') or new_likes != r.get('likes'):
        r['views']    = new_views
        r['likes']    = new_likes
        r['comments'] = new_comments
        r['lastRefreshed'] = m['ts'] or datetime.datetime.utcnow().isoformat() + 'Z'
        r['refreshStatus'] = 'ok'

        # Recalculate ER and CPV
        shares = r.get('shares') or 0
        saves  = r.get('saves') or 0
        eng = (new_likes or 0) + shares + (new_comments or 0) + saves
        r['engRate'] = round(eng / new_views * 100, 2) if new_views else None
        r['cpv']     = round(r['cost'] / new_views, 3) if (r.get('cost') and new_views) else None
        updated += 1

data['refreshedAt'] = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
data['rows'] = rows

with open(live_path, 'w') as f:
    json.dump(data, f, indent=2)

print(f"\nDone — updated {updated} rows, skipped {skipped} (no Apify match)")
print(f"Wrote {live_path}")
