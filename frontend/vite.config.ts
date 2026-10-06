import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://api:8000" },
    // bind mounts on Windows send no file events, so poll (slowly, to keep CPU low)
    watch: { usePolling: true, interval: 1000, ignored: ["**/node_modules/**"] },
  },
});
