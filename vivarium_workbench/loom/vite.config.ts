import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    // Vendored into vivarium-workbench (Task 8): build straight into
    // `loom/_dist` (this directory), which `vivarium_workbench.loom_assets
    // .asset_dir()` resolves to directly — NOT `bigraph_loom/_dist` (that
    // inner package's own `asset_dir()` shim is no longer the consumed path;
    // it's kept only for standalone/upstream parity). `npm run build`
    // refreshes `_dist` in place.
    outDir: '_dist',
    emptyOutDir: true,
    sourcemap: true,
  },
  test: {
    environment: 'jsdom',
  },
});
