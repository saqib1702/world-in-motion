import { useEffect, useMemo, useRef, useState } from "react";

export default function ChatPanel({
  agents,
  selectedAgentId,
  conversations,
  onSend,
  onSelectAgent,
  collapsible = true,
}) {
  const [draft, setDraft] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const [sending, setSending] = useState(false);
  const streamRef = useRef(null);

  const isCollapsed = collapsible && collapsed;

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.agent_id === selectedAgentId) || null,
    [agents, selectedAgentId]
  );

  const messages = useMemo(
    () => conversations[selectedAgentId] || [],
    [conversations, selectedAgentId]
  );

  // Pin to the newest message. Without this the reply lands below the fold and
  // the panel looks like it did nothing.
  useEffect(() => {
    const stream = streamRef.current;
    if (stream) stream.scrollTop = stream.scrollHeight;
  }, [messages.length, isCollapsed]);

  async function handleSubmit(event) {
    event.preventDefault();
    const text = draft.trim();
    if (!selectedAgent || !text || sending) return;
    setDraft("");
    setSending(true);
    try {
      await onSend(selectedAgent.agent_id, text);
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(event) {
    // Enter sends, Shift+Enter breaks the line. On a touch keyboard Enter should
    // insert a newline instead, since there is no modifier key available.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      const coarse = window.matchMedia?.("(pointer: coarse)").matches;
      if (!coarse) {
        event.preventDefault();
        handleSubmit(event);
      }
    }
  }

  return (
    <section className={`panel ${isCollapsed ? "collapsed" : ""}`}>
      <div className="panel-header">
        <div className="panel-title">Channel</div>
        {collapsible && (
          <button
            type="button"
            className="collapse-btn"
            aria-expanded={!isCollapsed}
            onClick={() => setCollapsed((value) => !value)}
            title={isCollapsed ? "Expand channel" : "Minimise channel"}
          >
            {isCollapsed ? "+" : "—"}
          </button>
        )}
      </div>

      {!isCollapsed && (
        <>
          {/* Tapping a small sphere on a phone is fiddly, so the roster is also
              reachable as a plain select. It stays on desktop as a keyboard
              path to selection, which clicking a 3D mesh does not provide. */}
          {onSelectAgent && agents.length > 0 && (
            <label className="agent-select">
              <span className="sr-only">Select nation</span>
              <select
                value={selectedAgentId || ""}
                onChange={(event) => onSelectAgent(event.target.value)}
              >
                <option value="" disabled>
                  Select a nation…
                </option>
                {agents.map((agent) => (
                  <option key={agent.agent_id} value={agent.agent_id}>
                    {agent.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          {!selectedAgent ? (
            <p className="muted">Tap a nation node, or pick one above, to open a channel.</p>
          ) : (
            <>
              <div className="chat-stream" ref={streamRef}>
                {messages.length === 0 ? (
                  <p className="muted">
                    No messages yet. Ask {selectedAgent.name} about its position.
                  </p>
                ) : (
                  messages.map((msg) => (
                    <div className={`chat-bubble ${msg.role}`} key={msg.id}>
                      <span className="chat-role">
                        {msg.role === "user" ? "Observer" : selectedAgent.name}
                      </span>
                      <p>{msg.text}</p>
                    </div>
                  ))
                )}
                {sending && (
                  <div className="chat-bubble agent pending">
                    <span className="chat-role">{selectedAgent.name}</span>
                    <p className="muted">Composing a response…</p>
                  </div>
                )}
              </div>
              <form onSubmit={handleSubmit} className="chat-form">
                <textarea
                  placeholder={`Message ${selectedAgent.name}...`}
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={handleKeyDown}
                  rows={2}
                />
                <button type="submit" disabled={sending || !draft.trim()}>
                  {sending ? "Sending…" : "Send"}
                </button>
              </form>
            </>
          )}
        </>
      )}
    </section>
  );
}
