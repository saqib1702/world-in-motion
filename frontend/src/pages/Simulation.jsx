import { useState } from "react";

import Scene3D from "../components/Scene3D";
import ChatPanel from "../components/ChatPanel";
import EventFeed from "../components/EventFeed";
import InjectPanel from "../components/InjectPanel";
import useMediaQuery, { COMPACT_QUERY } from "../hooks/useMediaQuery";

/* ---------------------------------------------------------------------------
 * The live board.
 *
 * This is the UI half of what used to be App.jsx. The data half moved to
 * hooks/useWorldState.js and is mounted once above the router, so navigating
 * away from this page and back does not drop the socket or lose the chat
 * history — the panels below are pure views over state that outlives them.
 *
 * Two layouts, one breakpoint (900px, shared with useMediaQuery.js):
 *
 *   Desktop — the scene fills the shell and the three panels float over it as
 *   overlays. There is room for all three at once, and the graph is the point.
 *
 *   Compact — the scene takes a fixed 52svh slice and the panels move into a
 *   tab dock below it. Floating a 340px panel over a 390px viewport leaves no
 *   graph to look at, so overlays are the wrong shape on a phone regardless of
 *   how well they scale.
 *
 * Only one panel is mounted at a time in compact mode. That is deliberate: it
 * halves the DOM under the canvas, and the panels keep their own draft/collapse
 * state, which is fine to reset on tab switch — the conversation itself lives in
 * useWorldState.
 * ------------------------------------------------------------------------- */

const DEFAULT_HEADLINE = "Strait closure disrupts container traffic";
const DEFAULT_DESCRIPTION =
  "A sudden naval blockade has closed a critical maritime chokepoint, and shipping insurers have suspended cover for the corridor.";

const DOCK_TABS = [
  { id: "graph", label: "Graph" },
  { id: "channel", label: "Channel" },
  { id: "inject", label: "Trigger" },
  { id: "feed", label: "Feed" }
];

export default function Simulation({ world }) {
  const {
    agents,
    relations,
    events,
    selectedAgentId,
    selectedAgent,
    setSelectedAgentId,
    conversations,
    status,
    busy,
    meta,
    sendMessage,
    triggerEvent
  } = world;

  const [headline, setHeadline] = useState(DEFAULT_HEADLINE);
  const [description, setDescription] = useState(DEFAULT_DESCRIPTION);
  const [tab, setTab] = useState("graph");

  const isCompact = useMediaQuery(COMPACT_QUERY);

  function handleTrigger() {
    triggerEvent({ headline, description });
  }

  const chat = (
    <ChatPanel
      agents={agents}
      selectedAgentId={selectedAgentId}
      conversations={conversations}
      onSend={sendMessage}
      onSelectAgent={setSelectedAgentId}
      collapsible={!isCompact}
    />
  );

  const inject = (
    <InjectPanel
      headline={headline}
      description={description}
      onHeadlineChange={setHeadline}
      onDescriptionChange={setDescription}
      onSubmit={handleTrigger}
      busy={busy}
      canWrite={meta.canWrite}
      writesRequireToken={meta.writesRequireToken}
      collapsible={!isCompact}
      // Injecting with a nation selected directs the event at it; with none
      // selected the whole roster reacts. Saying which is about to happen is
      // cheaper than making the user guess from the graph.
      targetLabel={selectedAgent ? selectedAgent.name : "all nations"}
    />
  );

  const feed = <EventFeed events={events} agents={agents} collapsible={!isCompact} />;

  return (
    <div className="sim-shell">
      <div className="sim-bar">
        <h1>Live board</h1>
        <div className="sim-bar-meta">
          <span className="pill pill-plain">
            {agents.length} actors · {relations.length} rows
          </span>
          <span className={`pill tone-${status.tone}`}>{status.text}</span>
        </div>
      </div>

      {isCompact && (
        <div className="sim-dock" role="tablist" aria-label="Board panels">
          {DOCK_TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              className={tab === item.id ? "active" : ""}
              onClick={() => setTab(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}

      {/* The canvas stays mounted in every compact tab. Unmounting it would
          destroy the WebGL context and re-run the force simulation from scratch
          on every tab switch, so it is hidden with CSS instead — and the
          RenderGovernor's IntersectionObserver stops it drawing while hidden,
          which is the part that actually costs battery. */}
      <div className={`scene-wrapper ${isCompact && tab !== "graph" ? "is-stowed" : ""}`}>
        <Scene3D
          agents={agents}
          relations={relations}
          selectedAgentId={selectedAgentId}
          onSelectAgent={setSelectedAgentId}
        />

        <div className="legend" aria-hidden="true">
          <span>
            <i className="allied" />
            allied
          </span>
          <span>
            <i className="neutral" />
            neutral
          </span>
          <span>
            <i className="hostile" />
            hostile
          </span>
        </div>

        {!isCompact && (
          <>
            <div className="overlay top-right">{chat}</div>
            <div className="overlay bottom-right">{inject}</div>
            <div className="overlay bottom-left">{feed}</div>
          </>
        )}
      </div>

      {isCompact && tab !== "graph" && (
        <div className="sim-sheet">
          {tab === "channel" && chat}
          {tab === "inject" && inject}
          {tab === "feed" && feed}
        </div>
      )}

      <p className="sim-disclaimer">{meta.disclaimer}</p>
    </div>
  );
}
