import { useState } from "react";

/**
 * Manual event injection. Extracted from App so the same panel can render both
 * as a floating overlay on desktop and inside the mobile dock sheet without the
 * markup being duplicated in two places and drifting apart.
 *
 * The `canWrite` gate comes from GET /meta. When the deployment sets
 * API_WRITE_TOKEN and this build has no VITE_API_TOKEN, every write returns 401,
 * so the honest thing is to disable the control and say why rather than let the
 * user compose an event and then hand them an auth error. The button is NOT the
 * security boundary — the server-side decorator is; this only stops the UI from
 * lying about what it can do.
 */
export default function InjectPanel({
  headline,
  description,
  onHeadlineChange,
  onDescriptionChange,
  onSubmit,
  busy,
  canWrite = true,
  writesRequireToken = false,
  collapsible = true,
  targetLabel,
}) {
  const [collapsed, setCollapsed] = useState(false);
  const isCollapsed = collapsible && collapsed;
  const canSubmit = canWrite && !busy && headline.trim() && description.trim();

  return (
    <section className={`panel inject-panel ${isCollapsed ? "collapsed" : ""}`}>
      <div className="panel-header">
        <div className="panel-title">Inject event</div>
        {collapsible && (
          <button
            type="button"
            className="collapse-btn"
            aria-expanded={!isCollapsed}
            onClick={() => setCollapsed((value) => !value)}
            title={isCollapsed ? "Expand event trigger" : "Minimise event trigger"}
          >
            {isCollapsed ? "+" : "—"}
          </button>
        )}
      </div>

      {!isCollapsed && (
        <>
          {!canWrite && (
            <p className="muted inject-locked">
              This deployment is read-only.{" "}
              {writesRequireToken
                ? "The server requires a write token that this build was not given."
                : "Writes are disabled."}{" "}
              The graph, roster and feed are all still live.
            </p>
          )}

          <label>
            Headline
            <input
              value={headline}
              onChange={(event) => onHeadlineChange(event.target.value)}
              placeholder="Headline the agents will react to"
              disabled={!canWrite}
            />
          </label>
          <label>
            Description
            <textarea
              rows={2}
              value={description}
              onChange={(event) => onDescriptionChange(event.target.value)}
              placeholder="One or two sentences of context"
              disabled={!canWrite}
            />
          </label>
          {targetLabel && canWrite && (
            <p className="muted inject-target">Directed at: {targetLabel}</p>
          )}
          <button type="button" disabled={!canSubmit} onClick={onSubmit}>
            {busy ? "Deliberating…" : "Inject + run tick"}
          </button>
        </>
      )}
    </section>
  );
}
