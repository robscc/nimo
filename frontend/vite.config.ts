import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: process.env.VITE_API_BASE_URL || "http://localhost:8099",
        changeOrigin: true,
        // SSE / streaming: disable proxy response buffering
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            // Ensure streaming responses are not buffered
            if (proxyRes.headers["content-type"]?.includes("text/event-stream")) {
              proxyRes.headers["x-accel-buffering"] = "no";
            }
          });
        },
      },
      "/health": {
        target: process.env.VITE_API_BASE_URL || "http://localhost:8099",
        changeOrigin: true,
      },
      "/uploads": {
        target: process.env.VITE_API_BASE_URL || "http://localhost:8099",
        changeOrigin: true,
      },
    },
  },
});
