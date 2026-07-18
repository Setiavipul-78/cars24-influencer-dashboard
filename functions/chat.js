export async function onRequestPost(context) {
  const { ANTHROPIC_API_KEY } = context.env;

  if (!ANTHROPIC_API_KEY) {
    return new Response(JSON.stringify({ error: 'API key not configured' }), {
      status: 500, headers: { 'Content-Type': 'application/json' }
    });
  }

  let body;
  try { body = await context.request.json(); }
  catch { return new Response(JSON.stringify({ error: 'Invalid JSON' }), { status: 400, headers: { 'Content-Type': 'application/json' } }); }

  const { question, dataContext, history = [] } = body;
  if (!question) return new Response(JSON.stringify({ error: 'No question' }), { status: 400, headers: { 'Content-Type': 'application/json' } });

  const systemPrompt = `You are CLOUT — the AI analyst embedded inside Cars24's Influencer Marketing Dashboard.
CLOUT stands for Campaign Lens for Outcome & Understanding Tracker.
You are sharp, data-driven, and speak like a senior marketing analyst who knows the numbers cold.

Your job: answer questions about the current influencer campaign data visible in the dashboard.
Keep answers concise, use numbers/percentages where relevant, and always reference month or creator names when available.
Use Indian number format for India data (Cr/L/K), and standard K/M for AU and UAE.
If the data doesn't contain enough info to answer, say so clearly rather than guessing.

CURRENT DASHBOARD DATA:
${dataContext || 'No data loaded yet.'}`;

  const messages = [
    ...history.map(h => ({ role: h.role, content: h.content })),
    { role: 'user', content: question }
  ];

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
        max_tokens: 512,
        system: systemPrompt,
        messages,
      }),
    });

    const data = await res.json();
    const answer = data?.content?.[0]?.text || 'Sorry, I couldn\'t generate a response.';
    return new Response(JSON.stringify({ answer }), {
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: 'Failed to reach Claude API', detail: e.message }), {
      status: 502, headers: { 'Content-Type': 'application/json' }
    });
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
