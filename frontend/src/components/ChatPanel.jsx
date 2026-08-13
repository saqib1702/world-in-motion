import { useMemo, useState } from "react";

export default function ChatPanel({ agents, selectedAgentId, conversations, onSend }) {
  const [draft, setDraft] = useState("");

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.agent_id === selectedAgentId) || null,
    [agents, selectedAgentId]
  );

  const messages = conversations[selectedAgentId] || [];

  async function handleSubmit(event) {
    event.preventDefault();
    const text = draft.trim();
    if (!selectedAgent || !text) return;
    setDraft("");
    await onSend(selectedAgent.agent_id, text);
  }

  return (
    <section className="panel chat-panel">
      <div className="panel-title">Nation Channel</div>
      {!selectedAgent ? (
        <p className="muted">Click a nation node to open a direct channel.</p>
      ) : (
        <>
          <div className="chat-agent-header">{selectedAgent.name}</div>
          <div className="chat-stream">
            {messages.length === 0 ? (
              <p className="muted">No messages yet.</p>
            ) : (
              messages.map((msg) => (
                <div className={`chat-bubble ${msg.role}`} key={msg.id}>
                  <span className="chat-role">{msg.role === "user" ? "You" : selectedAgent.name}</span>
                  <p>{msg.text}</p>
                </div>
              ))
            )}
          </div>
          <form onSubmit={handleSubmit} className="chat-form">
            <textarea
              placeholder={`Message ${selectedAgent.name}...`}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              rows={3}
            />
            <button type="submit">Send</button>
          </form>
        </>
      )}
    </section>
  );
}
