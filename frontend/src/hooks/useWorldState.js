import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { io } from "socket.io-client";

import {
  API_BASE,
  getAgents,
  getEvents,
  getMeta,
  getRelations,
  sendAgentMessage,
  triggerDemoEvent
} from "../api";

/* ---------------------------------------------------------------------------
 * The whole live data layer, in one hook.
 *
 * This used to live directly in App.jsx, which was fine when the app was one
 * screen. With four routes it has to be mounted exactly once — above the router
 * switch — for two reasons:
 *
 *   - One socket and one poll for the whole app. Mounting this per-page would
 *     open and tear down a Socket.IO session on every navigation, and each
 *     teardown/reconnect cycle costs a handshake plus a full refetch.
 *   - State survives navigation. Chat history, the current selection and the
 *     relation matrix are all still there when you come back from /method,
 *     which is also what makes returning to /simulation instant instead of
 *     showing an empty graph while it refetches.
 *
 * The cost is that the landing page holds a socket open too. That is deliberate:
 * the numbers in the hero readout are live, which is the entire point of the
 * project, and it is the same three small endpoints either way.
 * ------------------------------------------------------------------------- */

function makeMessage(role, text) {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role,
    text
  };
}

/* ---------------------------------------------------------------------------
 * Change detection.
 *
 * The safety-net poll refetches relations and events on a timer, and both
 * endpoints return a brand-new array every time even when nothing moved.
 * Calling setState with that array changes its identity, which restarted the
 * force simulation and re-triggered the graph pulse every few seconds — the
 * graph never sat still and the CPU never went idle.
 *
 * These signatures cover exactly the fields that are rendered, so an identical
 * payload becomes a no-op.
 * ------------------------------------------------------------------------- */

function relationsSignature(rows) {
  let out = "";
  for (const row of rows) {
    out += `${row.source_agent_id}>${row.target_agent_id}=${Number(row.score || 0).toFixed(2)};`;
  }
  return out;
}

function eventsSignature(rows) {
  let out = "";
  for (const row of rows) {
    const id = row.event_id || row.external_id || row.headline;
    out += `${id}@${row.timestamp || row.published_at || ""};`;
  }
  return out;
}

function agentsSignature(rows) {
  let out = "";
  for (const row of rows) out += `${row.agent_id}:${row.name};`;
  return out;
}

const POLL_INTERVAL_DEGRADED = 6000;
// Realtime pushes already cover relation changes, so the poll only needs to
// exist as a backstop against a missed event. Hammering the backend every six
// seconds when the websocket is healthy is wasted work on both ends.
const POLL_INTERVAL_REALTIME = 20000;

const FALLBACK_DISCLAIMER =
  "Nation agents are language models reasoning over real headlines. Their statements and relation shifts are simulated projections and do not represent the actual position of any government.";

export default function useWorldState() {
  const [agents, setAgents] = useState([]);
  const [relations, setRelations] = useState([]);
  const [events, setEvents] = useState([]);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [conversations, setConversations] = useState({});
  const [status, setStatus] = useState({ text: "Bootstrapping world state...", tone: "pending" });
  const [busy, setBusy] = useState(false);
  const [meta, setMeta] = useState({
    // Optimistic until /meta answers: assume writes are allowed, so the controls
    // do not flash disabled on every load. A 401 from an actual write still
    // surfaces the server's own message.
    canWrite: true,
    writesRequireToken: false,
    rosterSize: 0,
    disclaimer: FALLBACK_DISCLAIMER,
    loaded: false
  });

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.agent_id === selectedAgentId) || null,
    [agents, selectedAgentId]
  );

  // Read the current selection without making `loadAll` depend on it. Previously
  // loadAll listed selectedAgentId as a dependency and the socket effect listed
  // loadAll, so auto-selecting the first agent on boot changed the identity of
  // loadAll, which tore down and re-created the socket immediately after
  // connecting — visible as a reconnect storm and aborted connections.
  const selectedAgentIdRef = useRef("");
  useEffect(() => {
    selectedAgentIdRef.current = selectedAgentId;
  }, [selectedAgentId]);

  const signaturesRef = useRef({ agents: null, relations: null, events: null });

  const applyAgents = useCallback((rows) => {
    const signature = agentsSignature(rows);
    if (signature === signaturesRef.current.agents) return;
    signaturesRef.current.agents = signature;
    setAgents(rows);
  }, []);

  const applyRelations = useCallback((rows) => {
    const signature = relationsSignature(rows);
    if (signature === signaturesRef.current.relations) return;
    signaturesRef.current.relations = signature;
    setRelations(rows);
  }, []);

  const applyEvents = useCallback((rows) => {
    const signature = eventsSignature(rows);
    if (signature === signaturesRef.current.events) return;
    signaturesRef.current.events = signature;
    setEvents(rows);
  }, []);

  const loadAll = useCallback(
    async (signal) => {
      const [agentRows, relationRows, eventRows] = await Promise.all([
        getAgents({ signal }),
        getRelations({ signal }),
        getEvents({ signal })
      ]);
      applyAgents(agentRows);
      applyRelations(relationRows);
      applyEvents(eventRows);
      if (!selectedAgentIdRef.current && agentRows.length > 0) {
        setSelectedAgentId(agentRows[0].agent_id);
      }
    },
    [applyAgents, applyRelations, applyEvents]
  );

  // Asked once. Tells the UI whether this browser may write, so a read-only
  // deployment renders an honest notice instead of a button that returns 401.
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    getMeta({ signal: controller.signal })
      .then((info) => {
        if (active) setMeta({ ...info, loaded: true });
      })
      .catch(() => {
        // /meta is advisory. An older backend without the route, or a transient
        // failure, should not disable the interface.
        if (active) setMeta((prev) => ({ ...prev, loaded: true }));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, []);

  useEffect(() => {
    let active = true;
    const controllers = new Set();
    let timer = null;
    const realtimeRef = { current: false };

    const track = () => {
      const controller = new AbortController();
      controllers.add(controller);
      return controller;
    };

    async function start() {
      const controller = track();
      try {
        await loadAll(controller.signal);
        if (active) setStatus({ text: "Live", tone: "ok" });
      } catch (error) {
        if (active && error.name !== "AbortError") {
          setStatus({ text: `Load failed: ${error.message}`, tone: "error" });
        }
      } finally {
        controllers.delete(controller);
      }
    }

    start();

    const socket = io(API_BASE || undefined, {
      path: "/socket.io",
      // Polling FIRST, then let Socket.IO upgrade to websocket once the session
      // is established. Opening straight onto "websocket" means the very first
      // request is an HTTP Upgrade through the Vite dev proxy; if the backend is
      // still booting, or is running flask-socketio in threading mode without
      // `simple-websocket` installed, that upgrade is refused and surfaces as
      // ECONNABORTED with no usable session. Polling always succeeds, so the
      // app is live immediately and silently gets the websocket when available.
      transports: ["polling", "websocket"],
      upgrade: true,
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 8000,
      timeout: 10000
    });

    socket.on("connect", () => {
      const transport = socket.io.engine.transport.name;
      realtimeRef.current = transport === "websocket";
      setStatus({
        text: transport === "websocket" ? "Live via WebSocket" : "Live via polling",
        tone: "ok"
      });
      socket.io.engine.once("upgrade", () => {
        if (!active) return;
        realtimeRef.current = true;
        setStatus({ text: "Live via WebSocket", tone: "ok" });
      });
    });
    socket.on("disconnect", () => {
      realtimeRef.current = false;
      if (active) setStatus({ text: "Disconnected, retrying...", tone: "warn" });
    });
    socket.on("connect_error", (err) => {
      realtimeRef.current = false;
      if (active) setStatus({ text: `Realtime unavailable (${err.message}) — polling`, tone: "warn" });
    });
    socket.on("relation_stream_error", (info) => {
      // Backend telling us change streams are unavailable. Ticks still push, so
      // this is informational rather than fatal.
      console.warn("[realtime] relation stream degraded:", info);
    });
    socket.on("relation_update", async (data) => {
      if (data?.fullDocument?.source_agent_id && data?.fullDocument?.target_agent_id) {
        const doc = data.fullDocument;
        setRelations((prev) => {
          const idx = prev.findIndex(
            (r) =>
              r.source_agent_id === doc.source_agent_id &&
              r.target_agent_id === doc.target_agent_id
          );
          let next;
          if (idx >= 0) {
            next = [...prev];
            next[idx] = { ...next[idx], ...doc };
          } else {
            next = [...prev, doc];
          }
          applyRelations(next);
          return next;
        });
        return;
      }

      const controller = track();
      try {
        const updated = await getRelations({ signal: controller.signal });
        if (active) applyRelations(updated);
      } catch (_error) {
        // Keep existing state during transient network faults.
      } finally {
        controllers.delete(controller);
      }
    });

    // Safety net: if realtime is degraded, this keeps the graph and feed moving.
    // Self-scheduling rather than setInterval so the delay can follow the
    // transport, and so a slow response cannot cause requests to pile up.
    async function poll() {
      if (!active) return;

      if (!document.hidden) {
        const controller = track();
        try {
          const [latestEvents, latestRelations] = await Promise.all([
            getEvents({ signal: controller.signal }),
            getRelations({ signal: controller.signal })
          ]);
          if (active) {
            applyEvents(latestEvents);
            applyRelations(latestRelations);
          }
        } catch (_error) {
          // Polling is best effort.
        } finally {
          controllers.delete(controller);
        }
      }

      if (!active) return;
      timer = setTimeout(
        poll,
        realtimeRef.current ? POLL_INTERVAL_REALTIME : POLL_INTERVAL_DEGRADED
      );
    }

    timer = setTimeout(poll, POLL_INTERVAL_DEGRADED);

    // Coming back to a backgrounded tab should show current data immediately
    // rather than waiting out the remainder of the interval.
    const onVisible = () => {
      if (document.hidden || !active) return;
      if (timer) clearTimeout(timer);
      poll();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      active = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      for (const controller of controllers) controller.abort();
      controllers.clear();
      socket.disconnect();
    };
  }, [loadAll, applyEvents, applyRelations]);

  const sendMessage = useCallback(async (agentId, text) => {
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
  }, []);

  const triggerEvent = useCallback(
    async ({ headline, description }) => {
      const cleanHeadline = (headline || "").trim();
      const cleanDescription = (description || "").trim();
      if (!cleanHeadline || !cleanDescription) return;

      setBusy(true);
      setStatus({ text: "Running tick — agents deliberating...", tone: "pending" });
      try {
        const involved = selectedAgentIdRef.current
          ? [selectedAgentIdRef.current]
          : agents.map((a) => a.agent_id);
        await triggerDemoEvent({
          headline: cleanHeadline,
          description: cleanDescription,
          source: "demo_ui",
          event_type: "manual_demo_event",
          involved_agents: involved,
          payload: { fabricated: true }
        });
        await loadAll();
        setStatus({ text: "Tick processed", tone: "ok" });
      } catch (error) {
        setStatus({ text: `Trigger failed: ${error.message}`, tone: "error" });
      } finally {
        setBusy(false);
      }
    },
    [agents, loadAll]
  );

  return {
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
  };
}

export { FALLBACK_DISCLAIMER };
