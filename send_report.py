#!/usr/bin/env python3
"""Send the Cars24 Influencer Dashboard daily email report."""
import json, datetime, re, smtplib, os, sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

MO = ['January','February','March','April','May','June','July','August',
      'September','October','November','December']

def fmt(n):
    if n is None: return '--'
    n = float(n)
    def trim(s): return s.rstrip('0').rstrip('.')
    if n >= 1e7: return trim(f'{n/1e7:.2f}') + 'Cr'
    if n >= 1e5: return trim(f'{n/1e5:.2f}') + 'L'
    if n >= 1e3: return trim(f'{n/1e3:.1f}') + 'K'
    return str(int(round(n)))

def month_order(s):
    sl = s.lower()
    mi = next((i for i, m in enumerate(MO) if m.lower() in sl), -1)
    ym = re.search(r'(\d{4})', s)
    yr = int(ym.group(1)) if ym else 2025
    return yr * 100 + (mi + 1 if mi >= 0 else 0)

with open('live_data.json') as f: live = json.load(f)
with open('daily_snapshots.json') as f: snap_data = json.load(f)

rows = live['rows']
refreshed_utc = live.get('refreshedAt', '')
if refreshed_utc:
    from datetime import timezone, timedelta
    ist = datetime.datetime.fromisoformat(
        refreshed_utc.replace('Z', '+00:00')
    ).astimezone(timezone(timedelta(hours=5, minutes=30)))
    refreshed_ist = ist.strftime('%d %b %Y, %I:%M %p IST')
else:
    refreshed_ist = 'unknown'

today_ist = (datetime.datetime.utcnow() +
             datetime.timedelta(hours=5, minutes=30)).strftime('%d %b %Y')

all_months = sorted(
    set(r['liveMonth'] for r in rows if r.get('liveMonth')),
    key=month_order)
cur_month = all_months[-1] if all_months else None

cur_rows = [r for r in rows
            if r.get('liveMonth') == cur_month
            and r.get('liveStatus', '').lower() == 'live'] if cur_month else []

cur_views = sum(r.get('views') or 0 for r in cur_rows)
cur_spend = sum(r.get('cost') or 0 for r in cur_rows)
cur_cpv   = cur_spend / cur_views if cur_views else None
cur_eng   = sum((r.get('likes') or 0) + (r.get('comments') or 0) +
                (r.get('shares') or 0) + (r.get('saves') or 0) for r in cur_rows)
cur_er    = cur_eng / cur_views * 100 if cur_views else None

tot_views = sum(r.get('views') or 0 for r in rows)
tot_spend = sum(r.get('cost') or 0 for r in rows)
tot_cpv   = tot_spend / tot_views if tot_views else None

top5 = sorted([r for r in cur_rows if r.get('views')],
              key=lambda r: r['views'], reverse=True)[:5]

mom = {}
for r in rows:
    m = r.get('liveMonth')
    if not m: continue
    if m not in mom: mom[m] = {'views': 0, 'cost': 0, 'live': 0}
    mom[m]['views'] += r.get('views') or 0
    mom[m]['cost']  += r.get('cost') or 0
    if r.get('liveStatus', '').lower() == 'live': mom[m]['live'] += 1
mom_sorted = sorted(mom.items(), key=lambda x: month_order(x[0]))

snaps = sorted(snap_data.get('snapshots', []), key=lambda s: s['date'])[-7:]

W = 58
lines = []
lines.append('Cars24 Influencer Marketing -- Daily Report')
lines.append(f'Date: {today_ist}   |   Data refreshed: {refreshed_ist}')
lines.append('')
lines.append('=' * W)
lines.append(f'  {cur_month or "CURRENT MONTH"} SNAPSHOT  (live creators only)')
lines.append('=' * W)
lines.append(f'  Creators Live      {len(cur_rows)}')
lines.append(f'  Total Views        {fmt(cur_views)}')
lines.append(f'  Total Spend        Rs.{fmt(cur_spend)}')
lines.append(f'  Blended CPV        {"Rs."+format(cur_cpv,".2f") if cur_cpv else "--"}')
lines.append(f'  Engagement Rate    {format(cur_er,".2f")+"%" if cur_er else "--"}')
lines.append('')
lines.append('=' * W)
lines.append('  ALL-TIME CAMPAIGN TOTALS')
lines.append('=' * W)
lines.append(f'  Total Influencers  {len(rows)}')
lines.append(f'  Total Views        {fmt(tot_views)}')
lines.append(f'  Total Spend        Rs.{fmt(tot_spend)}')
lines.append(f'  Blended CPV        {"Rs."+format(tot_cpv,".2f") if tot_cpv else "--"}')
lines.append('')
lines.append('=' * W)
lines.append(f'  TOP 5 THIS MONTH -- {cur_month or ""} (by views)')
lines.append('=' * W)
for i, r in enumerate(top5, 1):
    cpv_str = f'  Rs.{r["cpv"]:.2f} CPV' if r.get('cpv') else ''
    lines.append(f'  {i}. {r["name"]:<32} {fmt(r.get("views")):>8} views{cpv_str}')
if not top5:
    lines.append('  No live data yet for this month.')
lines.append('')
lines.append('=' * W)
lines.append('  MONTH-ON-MONTH BREAKDOWN')
lines.append('=' * W)
lines.append(f'  {"Month":<18} {"Live":>5} {"Views":>9} {"Spend":>12} {"CPV":>8}')
lines.append('  ' + '-' * (W - 2))
for m, d in mom_sorted:
    cpv = d['cost'] / d['views'] if d['views'] else None
    tag = '  << current' if m == cur_month else ''
    lines.append(
        f'  {m:<18} {d["live"]:>5} {fmt(d["views"]):>9}'
        f' Rs.{fmt(d["cost"]):>9} {"Rs."+format(cpv,".2f") if cpv else "--":>8}{tag}')
lines.append('')
lines.append('=' * W)
lines.append('  DAILY REFRESH LOG (Last 7 Days)')
lines.append('=' * W)
lines.append(f'  {"Date":<12} {"Total Views":>12} {"Delta":>9} {"%":>7} {"Rows Upd":>9}')
lines.append('  ' + '-' * (W - 2))
for i, s in enumerate(snaps):
    prev  = snaps[i - 1] if i > 0 else None
    delta = s['totalViews'] - prev['totalViews'] if prev else None
    pct   = delta / prev['totalViews'] * 100 if prev and prev['totalViews'] else None
    delta_str = (('+' if delta >= 0 else '') + fmt(delta)) if delta is not None else 'first'
    pct_str   = (('+' if pct >= 0 else '') + f'{pct:.1f}%') if pct is not None else ''
    upd   = str(s.get('updatedRows', '?'))
    lines.append(
        f'  {s["date"]:<12} {fmt(s["totalViews"]):>12}'
        f' {delta_str:>9} {pct_str:>7} {upd:>9}')
if not snaps:
    lines.append('  No snapshot data yet.')
lines.append('')
lines.append(f'  Dashboard  https://cars24-influencer-dashboard.pages.dev')
lines.append(f'  Auto-generated by GitHub Actions after daily refresh.')

body = '\n'.join(lines)

subject  = f'Cars24 Influencer Dashboard -- Daily Report -- {today_ist}'
sender   = 'vipul.setia@cars24.com'
recipients = ['vipul.setia@cars24.com', 'yatika.malhotra@cars24.com']
pwd      = os.environ['GMAIL_APP_PASSWORD']

msg = MIMEMultipart()
msg['From']    = sender
msg['To']      = ', '.join(recipients)
msg['Subject'] = subject
msg.attach(MIMEText(body, 'plain'))

try:
    with smtplib.SMTP('smtp.gmail.com', 587) as s:
        s.ehlo()
        s.starttls()
        s.login(sender, pwd)
        s.sendmail(sender, recipients, msg.as_string())
    print(f'Report sent to: {", ".join(recipients)}')
except Exception as e:
    print(f'SMTP failed: {e}', file=sys.stderr)
    sys.exit(1)
