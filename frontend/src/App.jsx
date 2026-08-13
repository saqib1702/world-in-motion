import { useCallback, useEffect, useMemo, useState } from "react";
import { io } from "socket.io-client";

import { API_BASE, getAgents, getEvents, getRelations, sendAgentMessage, triggerDemoEvent } from "./api";
import RelationsGraph from "./components/RelationsGraph";
import ChatPanel from "./components/ChatPanel";
import EventFeed from "./components/EventFeed";

function makeMessage(role, text) {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role,
    text
  };
}

export default function App() {
  const [agents, setAgents] = useState([]);
  const [relations, setRelations] = useState([]);
  const [events, setEvents] = useState([]);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [conversations, setConversations] = useState({});
  const [demoHeadline, setDemoHeadline] = useState("Emergency Strait Blockade Disrupts Trade Lanes");
  const [demoDescription, setDemoDescription] = useState("A sudden naval blockade has disrupted shipping across a critical maritime chokepoint.");
  const [status, setStatus] = useState("Bootstrapping world state...");
  const [busy, setBusy] = useState(false);

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.agent_id === selectedAgentId) || null,
    [agents, selectedAgentId]
  );

  const loadAll = useCallback(async () => {
    const [agentRows, relationRows, eventRows] = await Promise.all([getAgents(), getRelations(), getEvents()]);
    setAgents(agentRows);
    setRelations(relationRows);
    setEvents(eventRows);
    if (!selectedAgentId && agentRows.length > 0) {
      setSelectedAgentId(agentRows[0].agent_id);
    }
  }, [selectedAgentId]);

  useEffect(() => {
    let active = true;

    async function start() {
      try {
        await loadAll();
        if (active) setStatus("Live");
      } catch (error) {
        if (active) setStatus(`Load failed: ${error.message}`);
      }
    }

    start();

    const socket = io(API_BASE || undefined, {
      path: "/socket.io",
      transports: ["websocket", "polling"]
    });

    socket.on("connect", () => setStatus("Live via WebSocket"));
    socket.on("disconnect", () => setStatus("Disconnected, retrying..."));
    socket.on("relation_update", async () => {
      try {
        const updated = await getRelations();
        if (active) setRelations(updated);
      } catch (_error) {
        // Keep existing state during transient network faults.
      }
    });

    const eventPoll = setInterval(async () => {
      try {
        const latest = await getEvents();
        if (active) setEvents(latest);
      } catch (_error) {
        // Polling is best effort.
      }
    }, 6000);

    return () => {
      active = false;
      clearInterval(eventPoll);
      socket.disconnect();
    };
  }, [loadAll]);

  async function handleSendMessage(agentId, text) {
    setConversations((prev) => {
      const base = prev[agentId] || [];
      return { ...prev, [agentId]: [...base, makeMessage("user", text)] };
    });

    try {
      const response = await sendAgentMessage(agentId, text);
      setConversations((prev) => {
        const base = prev[agentId] || [];
        return { ...prev, [agentId]: [...base, makeMessage("agent", response.reply || "No response")] };
      });
    } catch (error) {
      setConversations((prev) => {
        const base = prev[agentId] || [];
        return { ...prev, [agentId]: [...base, makeMessage("agent", `Error: ${error.message}`)] };
      });
    }
  }

  async function handleTriggerEvent() {
    const headline = demoHeadline.trim();
    const description = demoDescription.trim();
    if (!headline || !description) return;

    setBusy(true);
    setStatus("Triggering demo event tick...");
    try {
      const involved = selectedAgent ? [selectedAgent.agent_id] : agents.map((a) => a.agent_id);
      await triggerDemoEvent({
        headline,
        description,
        source: "demo_ui",
        event_type: "manual_demo_event",
        involved_agents: involved,
        payload: { fabricated: true }
      });
      await loadAll();
      setStatus("Demo event injected and tick processed");
    } catch (error) {
      setStatus(`Trigger failed: ${error.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <h1>World in Motion Command Deck</h1>
          <p>Real-time diplomacy simulation with live relation streaming</p>
        </div>
        <span className="status-pill">{status}</span>
      </header>

      <main className="layout-grid">
        <section className="panel graph-panel">
          <RelationsGraph
            agents={agents}
            relations={relations}
            selectedAgentId={selectedAgentId}
            onSelectAgent={setSelectedAgentId}
          />
        </section>

        <ChatPanel
          agents={agents}
          selectedAgentId={selectedAgentId}
          conversations={conversations}
          onSend={handleSendMessage}
        />

        <section className="panel inject-panel">
          <div className="panel-title">Trigger Demo Event</div>
          <label>
            Headline
            <input value={demoHeadline} onChange={(event) => setDemoHeadline(event.target.value)} />
          </label>
          <label>
            Description
            <textarea
              rows={4}
              value={demoDescription}
              onChange={(event) => setDemoDescription(event.target.value)}
            />
          </label>
          <button disabled={busy} onClick={handleTriggerEvent}>
            {busy ? "Injecting..." : "Trigger Event + Tick"}
          </button>
        </section>

        <EventFeed events={events} />
      </main>
    </div>
  );
}
