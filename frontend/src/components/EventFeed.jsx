export default function EventFeed({ events }) {
  return (
    <section className="panel event-panel">
      <div className="panel-title">Live Event Feed</div>
      <div className="event-list">
        {events.length === 0 ? (
          <p className="muted">Waiting for incoming events...</p>
        ) : (
          events.map((eventItem) => (
            <article className="event-card" key={eventItem.event_id || eventItem.external_id || `${eventItem.headline}-${eventItem.timestamp}`}>
              <header>
                <h4>{eventItem.headline || eventItem.title}</h4>
                <span>{new Date(eventItem.timestamp || eventItem.published_at || Date.now()).toLocaleString()}</span>
              </header>
              <p>{eventItem.description || eventItem.body || "No description provided."}</p>
              <footer>
                <span>{eventItem.source || "unknown"}</span>
                <span>{eventItem.event_type || "event"}</span>
              </footer>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
