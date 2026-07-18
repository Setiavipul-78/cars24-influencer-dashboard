#!/usr/bin/env python3
"""
Refresh script for AU and UAE Instagram reels (mirrors refresh_metrics.py for India).
Steps per country:
  1. Read current JSON file (au_live_data.json / uae_live_data.json)
  2. Scrape all Instagram reel links via Apify
  3. Update views, likes, comments, engRate, postedAt, liveMonth from Apify results
  4. Write updated JSON back to file
"""

import json, re, urllib.request, urllib.error, datetime, os, sys, time, base64, ssl, certifi

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "apify_api_U2idmzmhBnzlnMBi71su8BR6EzzVE30NEfF4")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "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")
GITHUB_OWNER = "Setiavipul-78"
GITHUB_REPO  = "cars24-influencer-dashboard"
ACTOR_ID     = "apify~instagram-scraper"
BATCH_SIZE   = 50
MAX_WAIT_S   = 600
POLL_S       = 10

SC_RE    = re.compile(r'/(?:reel|reels|p)/([A-Za-z0-9_-]+)(?:/|\?|$)')
INSTA_RE = re.compile(r'^https?://(www\.)?instagram\.com/', re.I)

def shortcode(url):
    m = SC_RE.search(url or '')
    return m.group(1) if m else None

def clean_insta_url(raw):
    candidates = [l.strip() for l in re.split(r'\s+', (raw or '').replace('\n', ' '))
                  if INSTA_RE.match(l.strip())]
    return (candidates[0].split('?')[0].rstrip('/') + '/') if candidates else ''

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

    # Collect scrapeable URLs
    urls = []
    for r in rows:
        link = clean_insta_url(r.get("videoLink", ""))
        if shortcode(link):
            urls.append(link)
    print(f"URLs with reel shortcode: {len(urls)}")

    apify = apify_scrape(urls)
    print(f"Apify returned: {len(apify)} shortcodes")

    updated = no_match = 0
    for r in rows:
        sc = shortcode(r.get("videoLink", ""))
        if not sc or sc not in apify:
            no_match += 1
            continue

        m = apify[sc]
        new_views    = m["views"]    if m["views"]    else r.get("views")
        new_likes    = m["likes"]    if m["likes"] is not None else r.get("likes")
        new_comments = m["comments"] if m["comments"] else r.get("comments")
        new_followers = m["followers"] if m["followers"] else r.get("followers")

        r.update({
            "views":         new_views,
            "likes":         new_likes,
            "comments":      new_comments,
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

        shares = r.get("shares") or 0
        saves  = r.get("saves")  or 0
        eng    = (new_likes or 0) + shares + (new_comments or 0) + saves
        r["engRate"] = round(eng / new_views * 100, 2) if new_views else None
        r["cpv"]     = round(r["cost"] / new_views, 3) if (r.get("cost") and new_views) else None
        updated += 1

    now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    data["refreshedAt"] = now_iso
    data["rows"] = rows

    with open(full_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Done — updated {updated} rows, {no_match} no Apify match")
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
