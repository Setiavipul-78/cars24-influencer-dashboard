// Cloudflare Pages Function — Slack bot endpoint
// Handles Slack Events API: URL verification + app_mention events
// Env vars needed: SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, ANTHROPIC_API_KEY

const GITHUB_RAW = 'https://raw.githubusercontent.com/Setiavipul-78/cars24-influencer-dashboard/main';

export async function onRequestPost(context) {
  const { request, env } = context;

  // Slack sends events as JSON
  let body;
  try { body = await request.json(); }
  catch { return text('Bad request', 400); }

  // ── URL verification (one-time when you configure the Slack app) ──
  if (body.type === 'url_verification') {
    return json({ challenge: body.challenge });
  }

  // ── Slack signature verification ──
  const sigSecret = env.SLACK_SIGNING_SECRET;
  if (sigSecret) {
    const ts  = request.headers.get('x-slack-request-timestamp') || '';
    const sig = request.headers.get('x-slack-signature') || '';
    // Basic replay attack guard (5 min window)
    if (Math.abs(Date.now() / 1000 - parseInt(ts)) > 300) {
      return text('Request too old', 403);
    }
    const encoder = new TextEncoder();
    const sigBase = `v0:${ts}:${JSON.stringify(body)}`;
    const key = await crypto.subtle.importKey(
      'raw', encoder.encode(sigSecret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
    );
    const computed = await crypto.subtle.sign('HMAC', key, encoder.encode(sigBase));
    const hex = 'v0=' + Array.from(new Uint8Array(computed)).map(b => b.toString(16).padStart(2, '0')).join('');
    if (hex !== sig) return text('Invalid signature', 403);
  }

  const event = body.event || {};

  // Only handle app_mention or direct message events
  if (!['app_mention', 'message'].includes(event.type)) {
    return json({ ok: true });
  }

  // Ignore bot's own messages
  if (event.bot_id || event.subtype) return json({ ok: true });

  const question = (event.text || '').replace(/<@[^>]+>/g, '').trim();
  if (!question) return json({ ok: true });

  const channel = event.channel;
  const thread_ts = event.thread_ts || event.ts;

  // Respond 200 to Slack immediately (3-sec SLA), then do async work
  context.waitUntil(handleQuestion(question, channel, thread_ts, env));
  return json({ ok: true });
}

async function handleQuestion(question, channel, thread_ts, env) {
  // Fetch live data from GitHub
  const [inData, auData, uaeData] = await Promise.all([
    fetchJson(`${GITHUB_RAW}/live_data.json`),
    fetchJson(`${GITHUB_RAW}/au_live_data.json`),
    fetchJson(`${GITHUB_RAW}/uae_live_data.json`),
  ]);

  const ctx = buildContext(inData, auData, uaeData);

  const systemPrompt = `You are GOAT — the AI campaign analyst for Cars24's Influencer Marketing Dashboard.
Answer questions about the influencer campaign data below. Be concise (2-4 sentences), cite specific numbers.
Use Indian format for India (Cr/L/K), K/M for AU and UAE. Today is ${new Date().toDateString()}.

DASHBOARD DATA:
${ctx}`;

  const answer = await callClaude(question, systemPrompt, env.ANTHROPIC_API_KEY);
  await postToSlack(channel, thread_ts, answer || '_Sorry, I could not generate a response._', env.SLACK_BOT_TOKEN);
}

function buildContext(inData, auData, uaeData) {
  const lines = [];

  if (inData) {
    const rows = inData.rows || [];
    const live = rows.filter(r => (r.liveStatus || '').toLowerCase() === 'live');
    const views = rows.reduce((s, r) => s + (r.views || 0), 0);
    const cost  = rows.reduce((s, r) => s + (r.cost  || 0), 0);
    const top3  = [...rows].sort((a,b) => (b.views||0) - (a.views||0)).slice(0,3);
    lines.push(`INDIA: ${live.length} live creators, ${(views/1e6).toFixed(1)}M total views, ₹${(cost/100000).toFixed(1)}L spend`);
    lines.push(`India top 3: ${top3.map(r => `${r.name} (${((r.views||0)/1000).toFixed(0)}K views, CPV ₹${r.cpv||'?'})`).join(', ')}`);
  }

  if (auData) {
    const rows = auData.rows || [];
    const live = rows.filter(r => (r.liveStatus || '').toLowerCase() === 'live');
    const views = rows.reduce((s, r) => s + (r.views || 0), 0);
    const top3  = [...rows].sort((a,b) => (b.views||0) - (a.views||0)).slice(0,3);
    lines.push(`AUSTRALIA: ${live.length} live creators, ${(views/1000).toFixed(0)}K total views`);
    lines.push(`AU top 3: ${top3.map(r => `${r.name} (${((r.views||0)/1000).toFixed(0)}K views)`).join(', ')}`);
  }

  if (uaeData) {
    const rows = uaeData.rows || [];
    const live = rows.filter(r => (r.liveStatus || '').toLowerCase() === 'live');
    const views = rows.reduce((s, r) => s + (r.views || 0), 0);
    const spend = rows.reduce((s, r) => s + (r.cost  || 0), 0);
    const top3  = [...rows].sort((a,b) => (b.views||0) - (a.views||0)).slice(0,3);
    lines.push(`UAE: ${live.length} live creators, ${(views/1000).toFixed(0)}K total views, AED ${spend.toLocaleString()} spend`);
    lines.push(`UAE top 3: ${top3.map(r => `${r.name} (${((r.views||0)/1000).toFixed(0)}K views, CPV AED ${r.cpv||'?'})`).join(', ')}`);
  }

  return lines.join('\n');
}

async function callClaude(question, systemPrompt, apiKey) {
  if (!apiKey) return '_ANTHROPIC_API_KEY not configured._';
  try {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 400,
        system: systemPrompt,
        messages: [{ role: 'user', content: question }],
      }),
    });
    if (!res.ok) return `_API error ${res.status}_`;
    const data = await res.json();
    return data?.content?.[0]?.text || null;
  } catch (e) {
    return `_Error: ${e.message}_`;
  }
}

async function postToSlack(channel, thread_ts, text, botToken) {
  if (!botToken) return;
  await fetch('https://slack.com/api/chat.postMessage', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${botToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel, thread_ts, text, mrkdwn: true }),
  });
}

async function fetchJson(url) {
  try {
    const res = await fetch(url);
    return res.ok ? res.json() : null;
  } catch { return null; }
}

export async function onRequestGet() {
  return text('GOAT Slack Bot is live. Configure your Slack app to POST events here.', 200);
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { 'Content-Type': 'application/json' }
  });
}
function text(msg, status = 200) {
  return new Response(msg, { status });
}
