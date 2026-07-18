#!/usr/bin/env python3
"""
Comprehensive Slack report for Cars24 Influencer Dashboard.
Reads India (live_data.json + daily_snapshots.json), AU and UAE (au/uae_live_data.json),
and delta_log.json, then posts a rich market-wise summary to a Slack webhook.
"""

import json, os, sys, datetime, urllib.request, ssl, certifi

SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_json(name):
    try:
        with open(os.path.join(SCRIPT_DIR, name)) as f:
            return json.load(f)
    except Exception as e:
        print(f"  Warning: could not load {name}: {e}", file=sys.stderr)
        return None


def arrow(v):
    if v is None: return ""
    if v > 0:  return f" ▲ *+{v:,}*"
    if v < 0:  return f" ▼ *{v:,}*"
    return " _(no change)_"


def pct(v, total):
    if not total: return ""
    return f" _{v/total*100:.1f}%_"


def fmt_spend(v, symbol="₹"):
    if not v: return "—"
    if v >= 10000000: return f"{symbol}{v/10000000:.1f} Cr"
    if v >= 100000:   return f"{symbol}{v/100000:.1f} L"
    if v >= 1000:     return f"{symbol}{v/1000:.1f}K"
    return f"{symbol}{v:,}"


def build_market_block(label, flag, key, delta_entry, rows, symbol="₹", spend_key="totalCost"):
    if not delta_entry:
        return [f"{flag} *{label}* — _no data yet_"]

    d = delta_entry.get("delta") or {}
    lines = [
        f"{flag} *{label}*",
        f"  • Creators live: *{delta_entry.get('creatorsLive', 0)}*{arrow(d.get('creatorsLive'))}",
        f"  • Total views:   *{delta_entry.get('totalViews', 0):,}*{arrow(d.get('totalViews'))}",
        f"  • Total likes:   *{delta_entry.get('totalLikes', 0):,}*{arrow(d.get('totalLikes'))}",
        f"  • Total comments:*{delta_entry.get('totalComments', 0):,}*{arrow(d.get('totalComments'))}",
    ]
    spend = delta_entry.get(spend_key) or delta_entry.get("totalSpend") or 0
    if spend:
        lines.append(f"  • Total spend:   *{fmt_spend(spend, symbol)}*")

    # Top 3 creators by views from live rows
    top3 = sorted([r for r in rows if (r.get("views") or 0) > 0],
                  key=lambda r: r.get("views") or 0, reverse=True)[:3]
    if top3:
        lines.append(f"  • Top creators:")
        for r in top3:
            v = r.get("views") or 0
            cpv = r.get("cpv")
            cpv_str = f" — CPV {symbol}{cpv:.2f}" if cpv else ""
            lines.append(f"    ↳ {r['name']} — {v:,} views{cpv_str}")

    return lines


def build_errors_block(au_rows, uae_rows, in_rows):
    errors = []
    for country, rows in [("AU", au_rows), ("UAE", uae_rows), ("India", in_rows)]:
        if rows is None:
            errors.append(f"⛔ {country}: data file missing")
            continue
        for r in rows:
            name = r.get("name", "?")
            status = (r.get("liveStatus") or "").lower()
            if status == "live":
                if not r.get("videoLink"):
                    errors.append(f"⚠️ {country} · {name}: Live but no video link")
                elif (r.get("views") or 0) == 0:
                    errors.append(f"⚠️ {country} · {name}: Live but 0 views (scrape may have failed)")
                elif r.get("refreshStatus") == "error":
                    errors.append(f"⚠️ {country} · {name}: Scrape error on last run")
    return errors


def post_to_slack(blocks_text):
    if not SLACK_WEBHOOK:
        print("SLACK_WEBHOOK_URL not set — skipping Slack post", file=sys.stderr)
        return
    payload = json.dumps({"text": blocks_text}).encode()
    req = urllib.request.Request(
        SLACK_WEBHOOK, data=payload,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
            print(f"Slack report sent (HTTP {r.status})")
    except Exception as e:
        print(f"Slack post failed: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    today = datetime.date.today().strftime("%d %b %Y")

    # Load data
    in_data   = load_json("live_data.json")
    au_data   = load_json("au_live_data.json")
    uae_data  = load_json("uae_live_data.json")
    delta_log = load_json("delta_log.json")

    in_rows  = (in_data  or {}).get("rows", [])
    au_rows  = (au_data  or {}).get("rows", [])
    uae_rows = (uae_data or {}).get("rows", [])

    # Get today's delta_log entry
    today_iso = datetime.date.today().isoformat()
    today_dl  = {}
    if delta_log:
        today_dl = next((e for e in reversed(delta_log.get("logs", []))
                         if e.get("date") == today_iso), {})

    in_entry  = today_dl.get("IN")
    au_entry  = today_dl.get("AU")
    uae_entry = today_dl.get("UAE")

    # Grand totals
    grand_views = sum([
        (in_entry  or {}).get("totalViews", 0),
        (au_entry  or {}).get("totalViews", 0),
        (uae_entry or {}).get("totalViews", 0),
    ])
    grand_creators = sum([
        (in_entry  or {}).get("creatorsLive", 0),
        (au_entry  or {}).get("creatorsLive", 0),
        (uae_entry or {}).get("creatorsLive", 0),
    ])

    errors = build_errors_block(au_rows, uae_rows, in_rows)

    lines = [
        f"*📊 Cars24 Influencer Dashboard — Daily Refresh ({today})*",
        f"_Global: {grand_creators} creators live · {grand_views:,} total views across all markets_",
        "",
    ]

    lines += build_market_block("India", "🇮🇳", "IN", in_entry, in_rows, symbol="₹", spend_key="totalCost")
    lines.append("")
    lines += build_market_block("Australia", "🇦🇺", "AU", au_entry, au_rows, symbol="A$", spend_key="totalSpend")
    lines.append("")
    lines += build_market_block("UAE", "🇦🇪", "UAE", uae_entry, uae_rows, symbol="AED ", spend_key="totalSpend")

    if errors:
        lines += ["", f"*⚠️ Action Required ({len(errors)} issues)*"]
        lines += [f"  {e}" for e in errors]
    else:
        lines += ["", "✅ *All scrapes completed successfully — no issues detected*"]

    lines += [
        "",
        f"_<https://cars24-influencer-dashboard.pages.dev|Open Dashboard> · Refreshed at {datetime.datetime.utcnow().strftime('%H:%M UTC')}_",
    ]

    post_to_slack("\n".join(lines))


if __name__ == "__main__":
    main()
