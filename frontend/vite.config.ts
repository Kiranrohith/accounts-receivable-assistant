import path from 'node:path'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The router plugin must come before the React plugin: it generates src/routeTree.gen.ts from src/routes/.
export default defineConfig({
  plugins: [tanstackRouter({ target: 'react', autoCodeSplitting: true }), react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
  test: {
    environment: 'jsdom',
    // Same origin as the API base URL, so jsdom's XHR makes no CORS preflight that MSW would have to answer.
    environmentOptions: { jsdom: { url: 'http://localhost:8000' } },
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    testTimeout: 15_000, // the first render of a file is slow while every file runs at once
    css: false,
  },
})
