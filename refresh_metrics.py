#!/usr/bin/env python3
"""
Refresh script — triggers a FRESH Apify Instagram scrape on every run.
csvPin=true rows are never overwritten (manually set from CSV).
URLs are batched in groups of 50 to stay within Apify limits.
"""

import json, re, urllib.request, urllib.error, datetime, os, sys, time

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "apify_api_U2idmzmhBnzlnMBi71su8BR6EzzVE30NEfF4")
ACTOR_ID    = "apify~instagram-scraper"
BATCH_SIZE  = 50    # URLs per Apify run
MAX_WAIT_S  = 600   # 10 min timeout per batch
POLL_S      = 10

SC_RE = re.compile(r'/(?:reel|reels|p)/([A-Za-z0-9_-]+)(?:/|\?|$)')

def shortcode(url):
    m = SC_RE.search(url or '')
    return m.group(1) if m else None

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "python"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def post_json(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP {e.code} error: {body}", file=sys.stderr)
        raise

# ── Load live_data.json ───────────────────────────────────────────────────────
script_dir = os.path.dirname(os.path.abspath(__file__))
live_path  = os.path.join(script_dir, "live_data.json")

with open(live_path) as f:
    data = json.load(f)

rows = data["rows"]

# ── Collect video URLs to scrape (skip csvPin rows) ───────────────────────────
urls_to_scrape = []
for r in rows:
    if r.get("csvPin"):
        continue
    link = r.get("videoLink", "")
    if shortcode(link) and link:
        urls_to_scrape.append(link.split("?")[0].rstrip("/") + "/")

urls_to_scrape = list(dict.fromkeys(urls_to_scrape))  # deduplicate
print(f"URLs to scrape: {len(urls_to_scrape)}")

if not urls_to_scrape:
    print("Nothing to scrape — exiting")
    sys.exit(0)

# ── Run Apify in batches ──────────────────────────────────────────────────────
apify = {}
batches = [urls_to_scrape[i:i+BATCH_SIZE] for i in range(0, len(urls_to_scrape), BATCH_SIZE)]
run_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"

for b_idx, batch in enumerate(batches):
    print(f"\nBatch {b_idx+1}/{len(batches)} — {len(batch)} URLs")

    payload = {
        "directUrls":   batch,
        "resultsType":  "posts",   # works for both /p/ and /reel/ direct URLs
        "resultsLimit": 1,         # 1 result per URL — we just need current stats
    }
    run_resp = post_json(run_url, payload)
    run_id   = run_resp["data"]["id"]
    ds_id    = run_resp["data"]["defaultDatasetId"]
    print(f"  Run ID: {run_id}")

    # Poll until done
    elapsed = 0
    status  = "RUNNING"
    while elapsed < MAX_WAIT_S:
        time.sleep(POLL_S)
        elapsed += POLL_S
        info   = fetch_json(f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}")
        status = info["data"]["status"]
        n      = info["data"].get("stats", {}).get("outputItems", "?")
        print(f"  [{elapsed}s] {status} — {n} items")
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break

    if status != "SUCCEEDED":
        print(f"  Batch {b_idx+1} failed with status {status} — skipping batch", file=sys.stderr)
        continue

    # Read results
    items = fetch_json(
        f"https://api.apify.com/v2/datasets/{ds_id}/items?token={APIFY_TOKEN}&format=json&limit=200"
    )
    print(f"  Got {len(items)} items")

    for it in items:
        raw = it.get("url") or it.get("inputUrl") or ""
        sc  = shortcode(raw) or it.get("shortCode") or it.get("shortcode") or ""
        if not sc:
            continue
        views = it.get("videoPlayCount") or 0
        lk    = it.get("likesCount")
        lk    = None if lk is None or lk < 0 else lk
        apify[sc] = {
            "views":    views,
            "likes":    lk,
            "comments": it.get("commentsCount", 0) or 0,
            "ts":       it.get("timestamp", ""),
        }

print(f"\nTotal shortcodes from Apify: {len(apify)}")

# ── Update rows ───────────────────────────────────────────────────────────────
updated     = 0
skipped_pin = 0
no_match    = 0

for r in rows:
    if r.get("csvPin"):
        skipped_pin += 1
        continue

    sc = shortcode(r.get("videoLink", ""))
    if not sc or sc not in apify:
        no_match += 1
        continue

    m            = apify[sc]
    new_views    = m["views"]    if m["views"]              else r.get("views")
    new_likes    = m["likes"]    if m["likes"] is not None   else r.get("likes")
    new_comments = m["comments"] if m["comments"]            else r.get("comments")

    r["views"]         = new_views
    r["likes"]         = new_likes
    r["comments"]      = new_comments
    r["lastRefreshed"] = m["ts"] or datetime.datetime.utcnow().isoformat() + "Z"
    r["refreshStatus"] = "ok"

    shares = r.get("shares") or 0
    saves  = r.get("saves")  or 0
    eng    = (new_likes or 0) + shares + (new_comments or 0) + saves
    r["engRate"] = round(eng / new_views * 100, 2) if new_views else None
    r["cpv"]     = round(r["cost"] / new_views, 3)  if (r.get("cost") and new_views) else None
    updated += 1

data["refreshedAt"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
data["rows"]        = rows

with open(live_path, "w") as f:
    json.dump(data, f, indent=2)

print(f"\nDone — updated {updated}, skipped (csvPin) {skipped_pin}, no Apify match {no_match}")
print(f"Wrote {live_path}")
