import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/health": "http://localhost:5000",
      "/agents": "http://localhost:5000",
      "/relations": "http://localhost:5000",
      "/events": "http://localhost:5000",
      "/engine": "http://localhost:5000",
      "/socket.io": {
        target: "http://localhost:5000",
        ws: true
      }
    }
  }
});
