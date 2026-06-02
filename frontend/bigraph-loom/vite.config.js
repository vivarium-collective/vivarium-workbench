import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
    plugins: [react()],
    base: './',
    build: {
        // Build straight into the dashboard's served static dir. The dashboard
        // vendors this source (frontend/bigraph-loom) and ships the built bundle
        // at vivarium_dashboard/static/bigraph-loom/ — `npm run build` updates it.
        outDir: '../../vivarium_dashboard/static/loom-explore',
        emptyOutDir: true,
        sourcemap: true,
    },
    test: {
        environment: 'jsdom',
    },
});
