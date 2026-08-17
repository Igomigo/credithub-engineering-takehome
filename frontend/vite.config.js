import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies API calls to the FastAPI backend on :8137, so there's
// no CORS to configure — fetch("/loans") from React just works.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5137,
    proxy: {
      "/loans": "http://localhost:8137",
      "/payment-events": "http://localhost:8137",
      "/audit-log": "http://localhost:8137",
      "/webhooks": "http://localhost:8137",
      "/health": "http://localhost:8137",
    },
  },
});
