import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://api:8000" },
    watch: { usePolling: true, interval: 300 }, // bind mounts on Windows send no file events
  },
});
