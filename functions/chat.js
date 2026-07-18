export async function onRequestPost(context) {
  const { ANTHROPIC_API_KEY } = context.env;

  if (!ANTHROPIC_API_KEY) {
    return json({ error: 'API key not configured' }, 500);
  }

  let body;
  try { body = await context.request.json(); }
  catch { return json({ error: 'Invalid JSON' }, 400); }

  const { question, dataContext, history = [] } = body;
  if (!question) return json({ error: 'No question provided' }, 400);

  const systemPrompt = `You are GOAT — the AI analyst embedded inside Cars24's Influencer Marketing Dashboard.
GOAT stands for Growth & Outcome Analysis Tool.
You are sharp, concise, and data-driven. Speak like a senior marketing analyst.

Answer questions about the current influencer campaign data below.
- Keep answers short and punchy (2-4 sentences max unless a list is clearly better)
- Always cite specific numbers, creator names, or months from the data
- Use Indian format for India (Cr/L/K), K/M for AU and UAE
- If data isn't available to answer, say so honestly in one line

CURRENT DASHBOARD DATA:
${dataContext || 'No data loaded yet.'}`;

  // Build a valid alternating-role message sequence.
  // Frontend pushes the current question to history before fetching, so drop any
  // trailing user message first to avoid two consecutive user entries.
  let hist = (history || []).filter(h => h && h.role && h.content);
  if (hist.length && hist[hist.length - 1].role === 'user') hist = hist.slice(0, -1);
  hist = hist.slice(-8);
  // Ensure sequence starts with a user message (Anthropic requires user-first).
  const firstUser = hist.findIndex(h => h.role === 'user');
  if (firstUser > 0) hist = hist.slice(firstUser);
  // Collapse any accidental same-role runs.
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
        'x-api-key': ANTHROPIC_API_KEY,
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
