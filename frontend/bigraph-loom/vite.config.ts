import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    // Build straight into the dashboard's served static dir. The dashboard
    // vendors this source (frontend/bigraph-loom) and ships the built bundle
    // at vivarium_dashboard/static/loom-explore/ — `npm run build` updates it.
    // (served path stays /loom-explore for now; URL rename is a follow-up).
    outDir: '../../vivarium_dashboard/static/loom-explore',
    emptyOutDir: true,
    sourcemap: true,
  },
  test: {
    environment: 'jsdom',
  },
});
