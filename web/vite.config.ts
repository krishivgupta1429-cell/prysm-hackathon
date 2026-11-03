import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
// Dev server: VITE_PORT and VITE_PROXY_TARGET let a second local stack run next to the default one (defaults unchanged).
const PROXY_TARGET = process.env.VITE_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig(({ mode }) => ({
  server: {
    host: "::",
    port: Number(process.env.VITE_PORT) || 3000,
    hmr: {
      overlay: false,
    },
    proxy: {
      '/prysm': PROXY_TARGET,
      '/auth': PROXY_TARGET,
      '/instagram': PROXY_TARGET,
      '/media': PROXY_TARGET,
      '/billing': PROXY_TARGET,
      '/health': PROXY_TARGET,
    },
  },
  plugins: [react(), mode === "development" && componentTagger()].filter(Boolean),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
}));
