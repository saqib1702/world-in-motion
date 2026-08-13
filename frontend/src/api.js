const API_BASE = import.meta.env.VITE_API_BASE || "";

async function parseJson(response) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error || `Request failed with status ${response.status}`);
  }
  return body;
}

export async function getAgents() {
  const response = await fetch(`${API_BASE}/agents`);
  const data = await parseJson(response);
  return data.agents || [];
}

export async function getRelations() {
  const response = await fetch(`${API_BASE}/relations`);
  const data = await parseJson(response);
  return data.relations || [];
}

export async function getEvents() {
  const response = await fetch(`${API_BASE}/events?limit=50`);
  const data = await parseJson(response);
  return data.events || [];
}

export async function sendAgentMessage(agentId, message) {
  const response = await fetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message })
  });
  return parseJson(response);
}

export async function triggerDemoEvent(payload) {
  const response = await fetch(`${API_BASE}/engine/tick`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ events: [payload] })
  });
  return parseJson(response);
}

export { API_BASE };
