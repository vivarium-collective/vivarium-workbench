import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { viteSingleFile } from 'vite-plugin-singlefile';

// Self-contained (single-file) build of the loom: every JS/CSS asset inlined into
// ONE index.html with no external references, so a composite baked in via the
// composite:load handshake opens with no server (download / email / static host /
// offline). Output: loom/_dist_single/ (gitignored; built by scripts/build_loom.sh).
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  base: './',
  build: { outDir: '_dist_single', emptyOutDir: true, assetsInlineLimit: 1e8, cssCodeSplit: false, sourcemap: false },
});
