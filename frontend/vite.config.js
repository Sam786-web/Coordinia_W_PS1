import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// start_all.py passes COORDINA_API_PORT when the backend runs on a non-default port
const apiPort = process.env.COORDINA_API_PORT || '8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': { target: `http://127.0.0.1:${apiPort}`, changeOrigin: true },
      '/ws': { target: `ws://127.0.0.1:${apiPort}`, ws: true },
    },
  },
})
