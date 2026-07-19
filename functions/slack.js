const DASHBOARD_BASE = 'https://cars24-influencer-dashboard.pages.dev';

export async function onRequestPost(context) {
  const { request, env } = context;

  const body = await request.text();
  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    return new Response('Bad Request', { status: 400 });
  }

  // Slack URL verification handshake (one-time when setting up the app)
  if (payload.type === 'url_verification') {
    return new Response(JSON.stringify({ challenge: payload.challenge }), {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // Verify Slack request signature
  const signingSecret = env.SLACK_SIGNING_SECRET;
  if (signingSecret) {
    const slackSig  = request.headers.get('x-slack-signature') || '';
    const timestamp = request.headers.get('x-slack-request-timestamp') || '';
    const valid = await verifySlackSignature(signingSecret, body, timestamp, slackSig);
    if (!valid) return new Response('Unauthorized', { status: 401 });
  }

  const event = payload.event;

  // Only handle @GOAT mentions; ignore bot's own messages
  if (!event || event.type !== 'app_mention' || event.bot_id) {
    return new Response('ok');
  }

  // Acknowledge immediately — Slack requires a response within 3 seconds
  context.waitUntil(handleMention(event, env));
  return new Response('ok');
}

// ─── Core handler ────────────────────────────────────────────────────────────

async function handleMention(event, env) {
  const question = event.text.replace(/<@[A-Z0-9]+>/g, '').trim();
  const channel   = event.channel;
  const threadTs  = event.thread_ts || event.ts;
  const botToken  = env.SLACK_BOT_TOKEN;

  if (!question) {
    await postSlack(channel, threadTs,
      '👋 Hey! Ask me anything about the Cars24 Influencer Dashboard.\n_e.g. "Which creator has the most views?" or "What\'s the CPV for India this month?"_',
      botToken);
    return;
  }

  // Show a typing indicator while we work
  await postSlack(channel, threadTs, '_GOAT is analyzing the data..._', botToken);

  const dataContext = await fetchDashboardData(env);

  // If data load failed, post the raw error directly — bypass Claude so the
  // real problem is visible in Slack instead of a generic "no data" response.
  if (!dataContext || dataContext.startsWith('No dashboard data')) {
    await postSlack(channel, threadTs,
      `⚠️ *GOAT couldn't load dashboard data* (env.ASSETS: ${env.ASSETS ? '✅' : '❌'})\n\`\`\`${dataContext}\`\`\``,
      botToken);
    return;
  }

  const apiKey = env.ANTHROPIC_API_KEY || env.ANTHROPIC_KEY;
  const answer = await callClaude(question, dataContext, apiKey);

  await postSlack(channel, threadTs, answer, botToken);
}

// ─── Fetch all dashboard data ────────────────────────────────────────────────
// Tries env.ASSETS (CF Pages static-asset binding) first; falls back to public fetch.

async function fetchDashboardData(env) {
  const sources = [
    { label: 'India Instagram', file: 'live_data.json',      country: 'IN', platform: 'Instagram' },
    { label: 'India YouTube',   file: 'india_yt_data.json',  country: 'IN', platform: 'YouTube'   },
    { label: 'Australia',       file: 'au_live_data.json',   country: 'AU', platform: 'Instagram' },
    { label: 'UAE',             file: 'uae_live_data.json',  country: 'UAE', platform: 'Instagram' },
  ];

  const settled = await Promise.allSettled(
    sources.map(async s => {
      let res;
      if (env && env.ASSETS) {
        // Read directly from the Pages static-asset store — no external HTTP hop
        res = await env.ASSETS.fetch(new Request(`https://asset.local/${s.file}`));
      } else {
        res = await fetch(`${DASHBOARD_BASE}/${s.file}`);
      }
      if (!res.ok) throw new Error(`HTTP ${res.status} fetching ${s.file}`);
      const data = await res.json();
      return { ...s, data };
    })
  );

  let ctx = '';
  const errs = [];

  for (let i = 0; i < settled.length; i++) {
    const result = settled[i];
    const src    = sources[i];
    if (result.status !== 'fulfilled') {
      errs.push(`${src.label}: ${result.reason?.message || String(result.reason)}`);
      continue;
    }
    const { label, country, platform, data } = result.value;
    const rows  = data.rows || [];
    const live  = rows.filter(r => (r.liveStatus || '').toLowerCase() === 'live');
    const totalViews = rows.reduce((s, r) => s + (r.views || 0), 0);
    const totalSpend = rows.reduce((s, r) => s + (r.cost  || 0), 0);

    ctx += `\n## ${label} (${country} / ${platform})\n`;
    ctx += `Creators: ${rows.length} total, ${live.length} live | Views: ${totalViews} | Spend: ${totalSpend}\n`;
    ctx += `Creators sorted by views (desc):\n`;

    const sorted = [...rows].sort((a, b) => (b.views || 0) - (a.views || 0));
    for (const r of sorted) {
      const cpv = r.cpv ? r.cpv.toFixed(3) : '—';
      const er  = r.engRate != null ? r.engRate.toFixed(1) + '%' : '—';
      ctx += `  ${r.name} | ${r.agency || '—'} | month=${r.liveMonth || '—'} | status=${r.liveStatus || '—'} | views=${r.views || 0} | cost=${r.cost || 0} | cpv=${cpv} | er=${er} | subs=${r.followers || 0}\n`;
    }
  }

  if (!ctx) {
    const errDetails = errs.length
      ? '\nFetch errors:\n' + errs.map(e => `- ${e}`).join('\n')
      : '';
    return `No dashboard data available.${errDetails}`;
  }
  return ctx;
}

// ─── Claude API call ─────────────────────────────────────────────────────────

async function callClaude(question, dataContext, apiKey) {
  if (!apiKey) {
    return '⚠️ GOAT is not configured — API key missing. Ask the dashboard admin to set `ANTHROPIC_API_KEY` in Cloudflare Pages.';
  }

  const system = `You are GOAT — the AI analyst for Cars24's Influencer Marketing Dashboard, embedded in Slack.
GOAT stands for Growth & Outcome Analysis Tool.
You are sharp, concise, and data-driven. Speak like a senior marketing analyst.

Rules:
- Keep answers short and punchy (2-4 sentences max, unless a ranked list is clearly better)
- Always cite specific numbers, creator names, or months from the data
- Use Indian format for India data (Cr/L/K), K/M for AU and UAE
- No hedging. No fluff. If data isn't available, say so in one line.

CURRENT DASHBOARD DATA:
${dataContext}`;

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
        max_tokens: 500,
        system,
        messages: [{ role: 'user', content: question }],
      }),
    });

    if (!res.ok) {
      const err = await res.text();
      return `⚠️ GOAT error (API ${res.status}): ${err.slice(0, 150)}`;
    }

    const data = await res.json();
    return data?.content?.[0]?.text || '⚠️ GOAT returned an empty response.';
  } catch (e) {
    return `⚠️ GOAT network error: ${e.message}`;
  }
}

// ─── Slack helpers ───────────────────────────────────────────────────────────

async function postSlack(channel, threadTs, text, botToken) {
  if (!botToken) return;
  await fetch('https://slack.com/api/chat.postMessage', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${botToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ channel, thread_ts: threadTs, text }),
  });
}

async function verifySlackSignature(secret, body, timestamp, slackSig) {
  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - parseInt(timestamp, 10)) > 300) return false;

  const sigBase  = `v0:${timestamp}:${body}`;
  const encoder  = new TextEncoder();
  const key      = await crypto.subtle.importKey(
    'raw', encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false, ['sign']
  );
  const sigBytes = await crypto.subtle.sign('HMAC', key, encoder.encode(sigBase));
  const hexSig   = 'v0=' + Array.from(new Uint8Array(sigBytes))
    .map(b => b.toString(16).padStart(2, '0')).join('');

  return hexSig === slackSig;
}
