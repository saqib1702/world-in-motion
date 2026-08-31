import { useMemo, useState } from "react";

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatDelta(value) {
  const delta = Number(value || 0);
  if (!delta) return null;
  return `${delta > 0 ? "+" : ""}${delta.toFixed(1)}`;
}

function EventCard({ item, agentNames }) {
  const [open, setOpen] = useState(false);
  const reactions = Array.isArray(item.agent_reactions) ? item.agent_reactions : [];

  return (
    <article className="event-card">
      <header>
        <h4>{item.headline || item.title}</h4>
        <span>{formatTime(item.timestamp || item.published_at || Date.now())}</span>
      </header>
      <p>{item.description || item.body || "No description provided."}</p>
      <footer>
        <span>{item.source || "unknown"}</span>
        {reactions.length > 0 ? (
          <button type="button" className="linkish" onClick={() => setOpen((value) => !value)}>
            {open ? "Hide" : `${reactions.length} reaction${reactions.length === 1 ? "" : "s"}`}
          </button>
        ) : (
          <span>{item.event_type || "event"}</span>
        )}
      </footer>

      {/* Reactions are the actual output of a tick — the reasoning behind every
          edge that just moved. They were being fetched and thrown away.
          Note the backend stores `agent_id` here, not a display name, so the
          roster is needed to render this readably. */}
      {open && reactions.length > 0 && (
        <ul className="reaction-list">
          {reactions.map((reaction, index) => {
            const delta = formatDelta(reaction.relation_delta);
            return (
              <li key={`${reaction.agent_id || index}-${reaction.timestamp || index}`}>
                <div className="reaction-head">
                  <strong>{agentNames.get(reaction.agent_id) || reaction.agent_id}</strong>
                  <span className="reaction-action">
                    {(reaction.action_type || "").replace(/_/g, " ")}
                  </span>
                  {delta && (
                    <span className={`reaction-delta ${Number(reaction.relation_delta) > 0 ? "up" : "down"}`}>
                      {delta}
                      {reaction.target_country && reaction.target_country !== "None"
                        ? ` → ${reaction.target_country}`
                        : ""}
                    </span>
                  )}
                </div>
                {reaction.reasoning && <p>{reaction.reasoning}</p>}
              </li>
            );
          })}
        </ul>
      )}
    </article>
  );
}

export default function EventFeed({ events, agents = [], collapsible = true }) {
  const [collapsed, setCollapsed] = useState(false);
  const isCollapsed = collapsible && collapsed;

  const agentNames = useMemo(
    () => new Map(agents.map((agent) => [agent.agent_id, agent.name])),
    [agents]
  );

  return (
    <section className={`panel ${isCollapsed ? "collapsed" : ""}`}>
      <div className="panel-header">
        <div className="panel-title">Event feed ({events.length})</div>
        {collapsible && (
          <button
            type="button"
            className="collapse-btn"
            aria-expanded={!isCollapsed}
            onClick={() => setCollapsed((value) => !value)}
            title={isCollapsed ? "Expand feed" : "Minimise feed"}
          >
            {isCollapsed ? "+" : "—"}
          </button>
        )}
      </div>

      {!isCollapsed && (
        <div className="event-list">
          {events.length === 0 ? (
            <p className="muted">Waiting for incoming events…</p>
          ) : (
            events.map((item) => (
              <EventCard
                key={item.event_id || item.external_id || `${item.headline}-${item.timestamp}`}
                item={item}
                agentNames={agentNames}
              />
            ))
          )}
        </div>
      )}
    </section>
  );
}
