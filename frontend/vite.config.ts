/// <reference types="vitest/config" />
import fs from 'node:fs'
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { type Plugin, defineConfig } from 'vite'

const backend = 'http://127.0.0.1:8000'

/** pdf.js data files the worker fetches by URL at runtime (src/lib/pdf.ts). */
const PDFJS_ROOT = path.resolve(import.meta.dirname, 'node_modules/pdfjs-dist')
const PDFJS_DIRS = ['cmaps', 'standard_fonts', 'wasm', 'iccs']
const PDFJS_BASE = 'pdfjs'

/**
 * Serves pdf.js CMaps (needed for CJK fonts), standard fonts, wasm decoders and ICC profiles
 * under /pdfjs/ in dev and copies them into the build, so no extra copy plugin is needed.
 */
function pdfjsAssets(): Plugin {
  return {
    name: 'pdfjs-assets',
    configureServer(server) {
      server.middlewares.use(`/${PDFJS_BASE}/`, (req, res, next) => {
        const relative = decodeURIComponent((req.url ?? '').split('?')[0]).replace(/^\/+/, '')
        const [dir] = relative.split('/')
        const file = path.resolve(PDFJS_ROOT, relative)
        if (!PDFJS_DIRS.includes(dir) || !file.startsWith(PDFJS_ROOT + path.sep) || !fs.existsSync(file)) {
          next()
          return
        }
        if (file.endsWith('.wasm')) res.setHeader('Content-Type', 'application/wasm')
        fs.createReadStream(file).pipe(res)
      })
    },
    generateBundle() {
      for (const dir of PDFJS_DIRS) {
        for (const name of fs.readdirSync(path.join(PDFJS_ROOT, dir))) {
          if (name.startsWith('LICENSE')) continue
          this.emitFile({
            type: 'asset',
            fileName: `${PDFJS_BASE}/${dir}/${name}`,
            source: fs.readFileSync(path.join(PDFJS_ROOT, dir, name)),
          })
        }
      }
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), pdfjsAssets()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': backend,
      '/health': backend,
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
