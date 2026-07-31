#!/usr/bin/env python3
"""
Refresh script for AU and UAE Instagram reels (mirrors refresh_metrics.py for India).
Steps per country:
  1. [UAE only] Sync liveStatus, videoLink, link, cost from Google Sheet
  2. Read current JSON file (au_live_data.json / uae_live_data.json)
  3. Scrape all Instagram reel links via Apify
  4. Update views, likes, comments, engRate, postedAt, liveMonth from Apify results
  5. Write updated JSON back to file
"""

import csv, io, json, re, urllib.parse, urllib.request, urllib.error
import datetime, os, sys, time, base64, ssl, certifi

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "apify_api_U2idmzmhBnzlnMBi71su8BR6EzzVE30NEfF4")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "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")
GITHUB_OWNER = "Setiavipul-78"
GITHUB_REPO  = "cars24-influencer-dashboard"
ACTOR_ID     = "apify~instagram-scraper"
BATCH_SIZE   = 50
MAX_WAIT_S   = 600
POLL_S       = 10

UAE_SHEET_ID  = "1_DEKX02I3Dh41B8nBtwPR1G-_4nt98WgDILZjzg3sp4"
UAE_SHEET_TAB = "Pan UAE POA - Instagram"

SC_RE    = re.compile(r'/(?:reel|reels|p)/([A-Za-z0-9_-]+)(?:/|\?|$)')
INSTA_RE = re.compile(r'^https?://(www\.)?instagram\.com/', re.I)

def shortcode(url):
    m = SC_RE.search(url or '')
    return m.group(1) if m else None

def clean_insta_url(raw):
    candidates = [l.strip() for l in re.split(r'\s+', (raw or '').replace('\n', ' '))
                  if INSTA_RE.match(l.strip())]
    return (candidates[0].split('?')[0].rstrip('/') + '/') if candidates else ''

def month_order(s):
    MO = ["january","february","march","april","may","june","july",
          "august","september","october","november","december"]
    sl = s.lower()
    mi = next((i + 1 for i, m in enumerate(MO) if m in sl), 0)
    ym = re.search(r"\d{4}", s)
    yr = int(ym.group()) if ym else 2025
    return yr * 100 + mi

def tier_of(f):
    if not f: return "Unknown"
    if f < 10_000:   return "Nano"
    if f < 100_000:  return "Micro"
    if f < 1_000_000: return "Macro"
    return "Mega"

def parse_k_number(s):
    """Parse '5.4k' → 5400, '524k' → 524000, '3000' → 3000."""
    if not s: return None
    s = s.strip().replace(',', '')
    m = re.match(r'^([\d.]+)([kKmM]?)$', s)
    if not m: return None
    n = float(m.group(1))
    suffix = m.group(2).lower()
    if suffix == 'k': return int(n * 1_000)
    if suffix == 'm': return int(n * 1_000_000)
    return int(n)

def parse_aed_cost(s):
    """Parse 'AED 2,500' → 2500."""
    if not s: return None
    nums = re.sub(r'[^\d.]', '', s)
    try: return float(nums) if nums else None
    except: return None

def sync_uae_from_sheet(rows):
    """Fetch the UAE master sheet and update liveStatus, videoLink, link, cost for each
    creator. Also inserts any new rows added to the sheet that aren't yet in the JSON.
    This is the step that was missing — it prevents stale Briefing status when a creator
    goes Live in the sheet but the nightly Apify run never picked them up (no videoLink
    in JSON yet → nothing to scrape → status never updated)."""
    url = (f"https://docs.google.com/spreadsheets/d/{UAE_SHEET_ID}"
           f"/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(UAE_SHEET_TAB)}")
    print("\n📊 Syncing from UAE sheet…")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=20) as r:
            text = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ⚠️  Sheet fetch failed ({e}) — skipping sync", file=sys.stderr)
        return rows

    reader = csv.reader(io.StringIO(text))
    raw = [r for r in reader if any(c.strip() for c in r)]
    if len(raw) < 2:
        print("  ⚠️  Sheet returned no data — skipping sync", file=sys.stderr)
        return rows

    hdrs = raw[0]

    def col(*names):
        for name in names:
            nl = name.lower()
            for i, h in enumerate(hdrs):
                if h.strip().lower() == nl: return i
            for i, h in enumerate(hdrs):
                if nl in h.strip().lower(): return i
        return -1

    C = {
        "name":       col("name"),
        "link":       col("channel link", "link"),
        "agency":     col("agency name", "agency"),
        "liveMonth":  col("live month"),
        "followers":  col("followers"),
        "cost":       col("final cost"),
        "liveStatus": col("live status"),
        "videoLink":  col("video live link"),
    }

    def g(row, key):
        i = C.get(key, -1)
        return row[i].strip() if 0 <= i < len(row) else ""

    # Index sheet rows by lowercase name
    sheet_by_name = {}
    for sr in raw[1:]:
        name = g(sr, "name")
        if name:
            sheet_by_name[name.lower()] = sr

    json_names = {r["name"].strip().lower() for r in rows}
    changes = 0

    for r in rows:
        key = r["name"].strip().lower()
        sr  = sheet_by_name.get(key)
        if not sr:
            print(f"  ⚠️  {r['name']!r} not found in sheet")
            continue

        updates = []

        new_status = g(sr, "liveStatus")
        if new_status and new_status != r.get("liveStatus"):
            r["liveStatus"] = new_status
            updates.append(f"liveStatus→{new_status}")

        sheet_video = clean_insta_url(g(sr, "videoLink"))
        json_video  = r.get("videoLink") or ""
        if sheet_video and not json_video:
            r["videoLink"] = sheet_video
            updates.append("videoLink added")
        elif sheet_video and not r.get("views"):
            r["videoLink"] = sheet_video
            updates.append("videoLink refreshed")

        new_link = g(sr, "link")
        if new_link:
            r["link"] = new_link

        new_agency = g(sr, "agency")
        if new_agency:
            r["agency"] = new_agency

        # Only set liveMonth from sheet if Apify hasn't already stamped a posting date
        new_month = g(sr, "liveMonth")
        if new_month and new_month != r.get("liveMonth") and not r.get("views"):
            r["liveMonth"]  = new_month
            r["monthOrder"] = month_order(new_month)
            updates.append(f"liveMonth→{new_month}")

        new_cost = parse_aed_cost(g(sr, "cost"))
        if new_cost is not None and new_cost != r.get("cost"):
            r["cost"] = new_cost
            updates.append(f"cost→{new_cost}")

        new_followers = parse_k_number(g(sr, "followers"))
        if new_followers and not r.get("followers"):
            r["followers"] = new_followers
            r["tier"]      = tier_of(new_followers)

        if updates:
            print(f"  ✏️  {r['name']}: {', '.join(updates)}")
            changes += 1

    # Add creators in the sheet but not yet in the JSON
    next_id = max((r.get("id", -1) for r in rows), default=-1) + 1
    for sname, sr in sheet_by_name.items():
        if sname in json_names:
            continue
        name      = g(sr, "name")
        cost      = parse_aed_cost(g(sr, "cost"))
        followers = parse_k_number(g(sr, "followers"))
        new_row = {
            "id":         next_id,
            "name":       name,
            "agency":     g(sr, "agency"),
            "category":   "",
            "followers":  followers,
            "link":       g(sr, "link"),
            "videoLink":  clean_insta_url(g(sr, "videoLink")),
            "views":      None,
            "likes":      None, "comments": None,
            "shares":     None, "saves":    None,
            "cost":       cost,
            "cpv":        None,
            "liveStatus": g(sr, "liveStatus"),
            "liveMonth":  g(sr, "liveMonth"),
            "monthOrder": month_order(g(sr, "liveMonth")),
            "region":     "UAE",
            "tier":       tier_of(followers),
            "engRate":    None, "postedAt": None,
        }
        rows.append(new_row)
        print(f"  ➕ Added new creator: {name}")
        next_id += 1
        changes += 1

    print(f"  Sheet sync done — {changes} change(s)")
    return rows


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "python"})
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as r:
        return r.read().decode('utf-8')

def fetch_json(url):
    return json.loads(fetch_text(url))

def post_json(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        raise

def apify_scrape(urls):
    """Scrape a list of Instagram URLs via Apify and return {shortcode: metrics} dict."""
    urls = list(dict.fromkeys(u for u in urls if shortcode(u)))
    if not urls:
        return {}

    result = {}
    run_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
    batches = [urls[i:i+BATCH_SIZE] for i in range(0, len(urls), BATCH_SIZE)]

    for b_idx, batch in enumerate(batches):
        print(f"  Batch {b_idx+1}/{len(batches)} — {len(batch)} URLs")
        run_resp = post_json(run_url, {
            "directUrls":   batch,
            "resultsType":  "posts",
            "resultsLimit": 1,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        })
        run_id = run_resp["data"]["id"]
        ds_id  = run_resp["data"]["defaultDatasetId"]
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

        if status != "SUCCEEDED":
            print(f"  Batch {b_idx+1} failed ({status}) — skipping", file=sys.stderr)
            continue

        items = fetch_json(
            f"https://api.apify.com/v2/datasets/{ds_id}/items"
            f"?token={APIFY_TOKEN}&format=json&limit=200"
        )
        print(f"  Got {len(items)} items")
        for it in items:
            raw = it.get("url") or it.get("inputUrl") or ""
            sc  = shortcode(raw) or it.get("shortCode") or it.get("shortcode") or ""
            if not sc:
                continue
            lk = it.get("likesCount")
            result[sc] = {
                "views":     it.get("videoPlayCount") or it.get("videoViewCount") or 0,
                "likes":     None if lk is None or lk < 0 else lk,
                "comments":  it.get("commentsCount", 0) or 0,
                "shares":    it.get("sharesCount"),
                "saves":     it.get("savesCount"),
                "followers": it.get("ownerFollowersCount") or 0,
                "ts":        it.get("timestamp", ""),
            }

    return result


def refresh_country(json_path, label):
    print(f"\n{'='*60}")
    print(f"  Refreshing {label}: {json_path}")
    print(f"{'='*60}")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    full_path  = os.path.join(script_dir, json_path)

    with open(full_path) as f:
        data = json.load(f)
    rows = data["rows"]

    # [UAE only] Step 1: sync liveStatus / videoLink / link / cost from Google Sheet
    # This catches creators whose status changed in the sheet since the last run,
    # and new creators added to the sheet that haven't been added to the JSON yet.
    if "uae" in label.lower():
        rows = sync_uae_from_sheet(rows)
        data["rows"] = rows

    # Collect scrapeable URLs
    urls = []
    for r in rows:
        link = clean_insta_url(r.get("videoLink", ""))
        if shortcode(link):
            urls.append(link)
    print(f"URLs with reel shortcode: {len(urls)}")

    apify = apify_scrape(urls)
    print(f"Apify returned: {len(apify)} shortcodes")

    updated = missed = no_link = 0
    for r in rows:
        sc = shortcode(r.get("videoLink", ""))
        if not sc:
            no_link += 1
            continue
        if sc not in apify:
            # Apify didn't return this video — keep every existing field as-is
            print(f"  ⚠️  {r['name']}: no match in Apify results — keeping last fetched data")
            missed += 1
            continue

        m = apify[sc]

        # Use Apify value if non-zero/non-None; fall back to last fetched data otherwise
        new_views     = m["views"]    if m["views"]                else r.get("views")
        new_likes     = m["likes"]    if m["likes"]    is not None else r.get("likes")
        new_comments  = m["comments"] if m["comments"] is not None else r.get("comments")
        new_shares    = m["shares"]   if m["shares"]   is not None else r.get("shares")
        new_saves     = m["saves"]    if m["saves"]    is not None else r.get("saves")
        new_followers = m["followers"] if m["followers"]           else r.get("followers")

        r.update({
            "views":         new_views,
            "likes":         new_likes,
            "comments":      new_comments,
            "shares":        new_shares,
            "saves":         new_saves,
            "followers":     new_followers or r.get("followers"),
            "lastRefreshed": datetime.datetime.utcnow().isoformat() + "Z",
            "refreshStatus": "ok",
        })
        if m["ts"]:
            r["postedAt"] = m["ts"]
            try:
                dt = datetime.datetime.fromisoformat(m["ts"].replace("Z", "+00:00"))
                r["liveMonth"]  = dt.strftime("%B %Y")
                r["monthOrder"] = dt.year * 100 + dt.month
            except Exception:
                pass

        eng = (new_likes or 0) + (new_shares or 0) + (new_comments or 0) + (new_saves or 0)
        # Keep last computed value when views are zero (scrape failure, not genuine 0)
        r["engRate"] = round(eng / new_views * 100, 2) if new_views else r.get("engRate")
        r["cpv"]     = round(r["cost"] / new_views, 3) if (r.get("cost") and new_views) else r.get("cpv")

        print(f"  ✅ {r['name']}: {new_views:,} views  "
              f"ER={r['engRate']}%  CPV={r['cost']}/{new_views}={r['cpv']}"
              if new_views else
              f"  ✅ {r['name']}: updated (views=0, kept last data)")
        updated += 1

    # Sort by campaign month then name, same as India
    rows.sort(key=lambda r: (r.get("monthOrder", 0), r.get("name", "")))

    now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    data["refreshedAt"] = now_iso
    data["rows"] = rows

    with open(full_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Done — updated {updated}, missed {missed} (kept last data), {no_link} no videoLink")
    print(f"Wrote {full_path}")

    # Compute snapshot metrics for delta log
    live_rows = [r for r in rows if (r.get("liveStatus") or "").lower() == "live"]
    snapshot = {
        "creatorsLive": len(live_rows),
        "totalViews":   sum(r.get("views") or 0 for r in rows),
        "totalLikes":   sum(r.get("likes") or 0 for r in rows),
        "totalComments":sum(r.get("comments") or 0 for r in rows),
        "totalSpend":   sum(r.get("cost") or 0 for r in live_rows),
    }
    country_key = "AU" if "australia" in label.lower() else "UAE"
    return json_path, full_path, country_key, snapshot


def push_to_github(file_path, repo_relative):
    """Push a local file to GitHub via the Contents API."""
    print(f"  Pushing {repo_relative} to GitHub…")
    api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{repo_relative}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github.v3+json",
        "User-Agent":    "python",
    }

    with open(file_path, "rb") as f:
        content = base64.b64encode(f.read()).decode()

    # Get current SHA
    sha = None
    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
            sha = json.loads(r.read())["sha"]
    except Exception:
        pass

    body = {
        "message": f"chore: refresh {repo_relative} {datetime.date.today().isoformat()}",
        "content": content,
        "branch":  "main",
    }
    if sha:
        body["sha"] = sha

    data_enc = json.dumps(body).encode()
    req = urllib.request.Request(api_url, data=data_enc,
                                 headers={**headers, "Content-Type": "application/json"},
                                 method="PUT")
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
        print(f"  GitHub push failed ({code}): {e.read().decode()[:200]}", file=sys.stderr)
        return
    print(f"  Pushed — HTTP {code}")


def update_delta_log(script_dir, country_key, new_snap):
    """Append today's snapshot to delta_log.json, computing delta vs previous entry."""
    log_path = os.path.join(script_dir, "delta_log.json")
    try:
        with open(log_path) as f:
            log_data = json.load(f)
    except FileNotFoundError:
        log_data = {"logs": []}

    today = datetime.date.today().isoformat()
    now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Find or create today's entry
    entry = next((e for e in log_data["logs"] if e["date"] == today), None)
    if entry is None:
        entry = {"date": today, "refreshedAt": now_iso}
        log_data["logs"].append(entry)
    entry["refreshedAt"] = now_iso

    # Find previous snapshot for this country to compute delta
    prev = None
    for e in reversed(log_data["logs"][:-1]):
        if country_key in e:
            prev = e[country_key]
            break

    delta = None
    if prev:
        delta = {k: new_snap[k] - prev.get(k, 0) for k in new_snap}

    entry[country_key] = {**new_snap, "delta": delta}

    # Keep last 30 days only
    log_data["logs"] = sorted(log_data["logs"], key=lambda e: e["date"])[-30:]

    with open(log_path, "w") as f:
        json.dump(log_data, f, indent=2)
    print(f"  Delta log updated for {country_key} (date={today})")
    return log_path


SKIP_PUSH = os.environ.get("SKIP_PUSH") == "1"  # set in GitHub Actions; git push done by workflow


def fmt_delta(val):
    if val is None or val == 0:
        return ""
    sign = "+" if val > 0 else ""
    return f" ({sign}{val:,})"


def post_slack(webhook_url, au_snap, uae_snap, au_log, uae_log, errors):
    today = datetime.date.today().strftime("%d %b %Y")
    au_d   = au_log.get("delta") or {}
    uae_d  = uae_log.get("delta") or {}

    lines = [
        f"*📊 Cars24 Influencer — Daily Refresh ({today})*",
        "",
        f"*🇦🇺 Australia*",
        f"• {au_snap['creatorsLive']} creators live",
        f"• {au_snap['totalViews']:,} total views{fmt_delta(au_d.get('totalViews'))}",
        f"• {au_snap['totalLikes']:,} likes{fmt_delta(au_d.get('totalLikes'))} · {au_snap['totalComments']:,} comments{fmt_delta(au_d.get('totalComments'))}",
        "",
        f"*🇦🇪 UAE*",
        f"• {uae_snap['creatorsLive']} creators live",
        f"• {uae_snap['totalViews']:,} total views{fmt_delta(uae_d.get('totalViews'))}",
        f"• {uae_snap['totalLikes']:,} likes{fmt_delta(uae_d.get('totalLikes'))} · {uae_snap['totalComments']:,} comments{fmt_delta(uae_d.get('totalComments'))}",
        f"• AED {uae_snap['totalSpend']:,} total spend",
    ]
    if errors:
        lines += ["", f"*⚠️ Issues ({len(errors)})*"] + [f"• {e}" for e in errors]
    else:
        lines.append("\n✅ All creators scraped successfully")

    payload = json.dumps({"text": "\n".join(lines)}).encode()
    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
            print(f"  Slack notification sent (HTTP {r.status})")
    except Exception as e:
        print(f"  Slack notification failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    files_pushed = []
    errors = []

    # AU
    _, au_path, au_key, au_snap = refresh_country("au_live_data.json", "Australia")
    files_pushed.append(("au_live_data.json", au_path))

    # UAE
    _, uae_path, uae_key, uae_snap = refresh_country("uae_live_data.json", "UAE")
    files_pushed.append(("uae_live_data.json", uae_path))

    # Update delta log
    au_log_path  = update_delta_log(script_dir, au_key, au_snap)
    uae_log_path = update_delta_log(script_dir, uae_key, uae_snap)
    files_pushed.append(("delta_log.json", os.path.join(script_dir, "delta_log.json")))

    # Push to GitHub (skip in GitHub Actions — workflow does git push instead)
    if not SKIP_PUSH:
        print("\n── Pushing to GitHub ──")
        for repo_rel, local_path in files_pushed:
            push_to_github(local_path, repo_rel)
    else:
        print("\n── SKIP_PUSH=1 — GitHub Actions will commit & push ──")

    # Slack notification
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if slack_url:
        print("\n── Sending Slack report ──")
        # Read back delta log to get delta values
        try:
            with open(os.path.join(script_dir, "delta_log.json")) as f:
                dl = json.load(f)
            today_str = datetime.date.today().isoformat()
            today_entry = next((e for e in dl["logs"] if e["date"] == today_str), {})
            au_log_data  = today_entry.get("AU", {})
            uae_log_data = today_entry.get("UAE", {})
        except Exception:
            au_log_data = uae_log_data = {}
        post_slack(slack_url, au_snap, uae_snap, au_log_data, uae_log_data, errors)
    else:
        print("\n── SLACK_WEBHOOK_URL not set — skipping Slack report ──")

    print("\nAll done.")
