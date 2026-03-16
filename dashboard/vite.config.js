import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/agents": "http://localhost:8000",
      "/graph": "http://localhost:8000",
      "/tasks": "http://localhost:8000",
      "/register": "http://localhost:8000",
      "/deregister": "http://localhost:8000",
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
})
