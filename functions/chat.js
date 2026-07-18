const ALLOWED_ORIGINS = [
  'https://cars24-influencer-dashboard.pages.dev',
  'https://cars24-influencer-dashboard.pages.dev/',
];

export async function onRequestPost(context) {
  const { request } = context;

  // Block requests from outside the dashboard (bots, scrapers, abuse)
  const origin  = request.headers.get('origin')  || '';
  const referer = request.headers.get('referer') || '';
  const isFromDashboard = ALLOWED_ORIGINS.some(o => origin.startsWith(o) || referer.startsWith(o));
  if (!isFromDashboard) {
    return json({ error: 'Forbidden' }, 403);
  }

  // Accept the key under any of the common naming conventions
  const apiKey = context.env['ANTHROPIC_API_KEY']
              || context.env['ANTHROPIC_KEY']
              || context.env['anthropic_api_key'];

  if (!apiKey) {
    const presentKeys = Object.keys(context.env || {}).join(', ') || 'none';
    return json({ error: 'API key not configured', hint: `Env vars visible: ${presentKeys}` }, 500);
  }

  let body;
  try { body = await request.json(); }
  catch { return json({ error: 'Invalid JSON' }, 400); }

  const { question, dataContext, history = [] } = body;
  if (!question) return json({ error: 'No question provided' }, 400);

  // Cap dataContext to ~12,000 chars (~3,000 tokens) — enough for all 190+ creators
  const ctxTrimmed = (dataContext || 'No data loaded yet.').slice(0, 12000);

  const systemPrompt = `You are GOAT — the AI analyst embedded inside Cars24's Influencer Marketing Dashboard.
GOAT stands for Growth & Outcome Analysis Tool.
You are sharp, concise, and data-driven. Speak like a senior marketing analyst.

Answer questions about the current influencer campaign data below.
- Keep answers short and punchy (2-4 sentences max unless a list is clearly better)
- Always cite specific numbers, creator names, or months from the data
- Use Indian format for India (Cr/L/K), K/M for AU and UAE
- If data isn't available to answer, say so honestly in one line

CURRENT DASHBOARD DATA:
${ctxTrimmed}`;

  // Build a valid alternating-role message sequence.
  let hist = (history || []).filter(h => h && h.role && h.content);
  if (hist.length && hist[hist.length - 1].role === 'user') hist = hist.slice(0, -1);
  hist = hist.slice(-8);
  const firstUser = hist.findIndex(h => h.role === 'user');
  if (firstUser > 0) hist = hist.slice(firstUser);
  const deduped = [];
  for (const h of hist) {
    if (!deduped.length || deduped[deduped.length - 1].role !== h.role) {
      deduped.push({ role: h.role, content: h.content });
    }
  }
  const messages = [...deduped, { role: 'user', content: question }];

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
        system: systemPrompt,
        messages,
      }),
    });

    if (!res.ok) {
      const errText = await res.text();
      return json({ error: `Anthropic API error ${res.status}`, detail: errText.slice(0, 200) }, 502);
    }

    const data = await res.json();
    const answer = data?.content?.[0]?.text;
    if (!answer) return json({ error: 'Empty response from Claude', raw: JSON.stringify(data).slice(0, 300) }, 502);

    return json({ answer });
  } catch (e) {
    return json({ error: 'Network error reaching Claude API', detail: e.message }, 502);
  }
}

export async function onRequestOptions() {
  return new Response(null, {
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    }
  });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
  });
}
