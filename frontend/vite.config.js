import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// IMPORTANT: target 127.0.0.1, not "localhost".
// Node 17+ (and therefore Vite) no longer reorders DNS results to prefer IPv4,
// so on Windows "localhost" usually resolves to ::1 first. Flask binds to
// FLASK_HOST, which defaults to 127.0.0.1 — IPv4 only — so the proxy's first
// connection attempt hits a closed IPv6 port and the request dies with
// ECONNREFUSED / ECONNABORTED before it ever reaches Flask. Pinning the literal
// IPv4 address removes the ambiguity. Override with VITE_PROXY_TARGET if the
// backend runs elsewhere.
const target = process.env.VITE_PROXY_TARGET || "http://127.0.0.1:5000";

// Shared options so every proxied route behaves identically.
const httpProxy = {
  target,
  changeOrigin: true,
  // Surface backend-down as a readable message instead of an opaque 500.
  configure: (proxy) => {
    proxy.on("error", (err, _req, res) => {
      const hint =
        err.code === "ECONNREFUSED" || err.code === "ECONNABORTED"
          ? ` — is the Flask backend running on ${target}?`
          : "";
      console.warn(`[vite-proxy] ${err.code || err.message}${hint}`);
      if (res && "writeHead" in res && !res.headersSent) {
        res.writeHead(502, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify({
            error: "backend_unreachable",
            code: err.code || "PROXY_ERROR",
            target
          })
        );
      }
    });
  }
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Bind the dev server on IPv4 too, so http://localhost:5173 and
    // http://127.0.0.1:5173 both work in the browser.
    host: "127.0.0.1",
    // These keys are prefix matches, and they now share a namespace with the
    // client-side routes in src/router.jsx (/, /simulation, /nations, /method).
    // Anything listed here goes to Flask; anything else falls through to Vite's
    // SPA fallback and renders the app. Note /meta and /method: neither is a
    // prefix of the other ("met-a" vs "met-h"), so they do not collide — but a
    // route named /metrics would be swallowed by /meta, so check before adding
    // a page whose path starts with an API prefix.
    proxy: {
      "/health": httpProxy,
      "/meta": httpProxy,
      "/agents": httpProxy,
      "/relations": httpProxy,
      "/events": httpProxy,
      "/engine": httpProxy,
      "/socket.io": {
        ...httpProxy,
        // Required for the Socket.IO websocket upgrade to be forwarded.
        ws: true
      }
    }
  }
});
