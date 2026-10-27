import { defineConfig, loadEnv, type Plugin } from 'vite';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Most catalog models are Draco-compressed. Serve three's own decoder at /draco/ (dev and build)
// so decoding never depends on a third-party CDN.
const draco = (name: string) => fileURLToPath(new URL(`./node_modules/three/examples/jsm/libs/draco/gltf/${name}`, import.meta.url));
const decoderFiles = ['draco_decoder.js', 'draco_decoder.wasm', 'draco_wasm_wrapper.js'];
function dracoDecoder(): Plugin {
  return {
    name: 'lucid-draco-decoder',
    configureServer(server) {
      server.middlewares.use('/draco', (request, response, next) => {
        const name = (request.url ?? '').slice(1).split('?')[0];
        if (!decoderFiles.includes(name)) return next();
        response.setHeader('Content-Type', name.endsWith('.wasm') ? 'application/wasm' : 'text/javascript');
        response.end(readFileSync(draco(name)));
      });
    },
    generateBundle() {
      for (const name of decoderFiles) this.emitFile({ type: 'asset', fileName: `draco/${name}`, source: readFileSync(draco(name)) });
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env };
  const target = env.LUCID_BACKEND_URL || 'http://127.0.0.1:8000';
  if (!!env.LUCID_TLS_CERT !== !!env.LUCID_TLS_KEY) throw new Error('Set both TLS certificate and key');
  const https = env.LUCID_TLS_CERT && env.LUCID_TLS_KEY
    ? { cert: readFileSync(env.LUCID_TLS_CERT), key: readFileSync(env.LUCID_TLS_KEY) } : undefined;
  // Preserve the browser Origin: the backend must explicitly allow it.
  const proxy = { '/api': { target }, '/ws': { target, ws: true } };
  return {
    plugins: [dracoDecoder()],
    server: { host: env.LUCID_FRONTEND_HOST || 'localhost', port: 5173, strictPort: true, https, proxy },
    preview: { host: 'localhost', port: 5173, strictPort: true, https, proxy },
  };
});
