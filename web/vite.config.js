import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server proxies API calls to the FastAPI backend, so the app fetches
// same-origin paths in every environment.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
