const API_BASE = import.meta.env.VITE_API_BASE || "";

// Write endpoints (POST /engine/tick, POST /agents/<id>/chat) are token-gated on
// the server because each one spends Gemini quota. Anything Vite inlines into
// the bundle is readable by every visitor, so this is only meaningful for a
// personal or demo deployment. On a genuinely public one, leave VITE_API_TOKEN
// unset: reads still work, and the UI degrades to a read-only view instead of
// offering buttons that answer 401.
const API_TOKEN = import.meta.env.VITE_API_TOKEN || "";

function writeHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (API_TOKEN) headers.Authorization = `Bearer ${API_TOKEN}`;
  return headers;
}

async function parseJson(response) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    // The server's own message is the useful one — it distinguishes "no token
    // configured on this deployment" from "wrong token" from "slow down".
    if (response.status === 401) {
      throw new Error(
        body.error || "This deployment is read-only: writes need an API token."
      );
    }
    if (response.status === 429) {
      const wait = response.headers.get("Retry-After");
      throw new Error(
        wait
          ? `Rate limited — try again in ${wait}s.`
          : "Rate limited — too many requests."
      );
    }
    throw new Error(body.error || `Request failed with status ${response.status}`);
  }
  return body;
}

// Every read accepts an AbortSignal. Without one, a slow response and the next
// poll overlap: two fetches race and the older one can land last, overwriting
// fresh state with stale data.
export async function getAgents(options = {}) {
  const response = await fetch(`${API_BASE}/agents`, { signal: options.signal });
  const data = await parseJson(response);
  return data.agents || [];
}

export async function getRelations(options = {}) {
  const response = await fetch(`${API_BASE}/relations`, { signal: options.signal });
  const data = await parseJson(response);
  return data.relations || [];
}

export async function getEvents(options = {}) {
  // The feed shows a scrolling recent history, not an archive. Each document
  // carries its full agent_reactions array, so a high limit means a large
  // payload re-downloaded on every poll for rows nobody scrolls to.
  const limit = options.limit || 40;
  const response = await fetch(`${API_BASE}/events?limit=${limit}`, { signal: options.signal });
  const data = await parseJson(response);
  return data.events || [];
}

// Asked once on load. `canWrite` decides whether the UI shows live controls or
// an honest read-only notice, and `disclaimer` is served by the backend so the
// "these are simulated projections" wording cannot drift between the two.
export async function getMeta(options = {}) {
  const response = await fetch(`${API_BASE}/meta`, {
    signal: options.signal,
    headers: API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {}
  });
  const data = await parseJson(response);
  return {
    app: data.app || "World in Motion",
    rosterSize: data.roster_size || 0,
    writesRequireToken: Boolean(data.writes_require_token),
    // True when this browser will actually be allowed to write: either the
    // server has no gate, or we hold a token it accepts.
    canWrite: !data.writes_require_token || Boolean(data.authenticated),
    disclaimer: data.disclaimer || ""
  };
}

export async function sendAgentMessage(agentId, message) {
  const response = await fetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}/chat`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ message })
  });
  return parseJson(response);
}

export async function triggerDemoEvent(payload) {
  const response = await fetch(`${API_BASE}/engine/tick`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ events: [payload] })
  });
  return parseJson(response);
}

export { API_BASE, API_TOKEN };
