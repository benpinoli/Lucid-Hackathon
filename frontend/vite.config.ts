import { defineConfig, loadEnv } from 'vite';
import { readFileSync } from 'node:fs';

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env };
  const target = env.LUCID_BACKEND_URL || 'http://127.0.0.1:8000';
  if (!!env.LUCID_TLS_CERT !== !!env.LUCID_TLS_KEY) throw new Error('Set both TLS certificate and key');
  const https = env.LUCID_TLS_CERT && env.LUCID_TLS_KEY
    ? { cert: readFileSync(env.LUCID_TLS_CERT), key: readFileSync(env.LUCID_TLS_KEY) } : undefined;
  // Preserve the browser Origin: the backend must explicitly allow it.
  const proxy = { '/api': { target }, '/ws': { target, ws: true } };
  return {
    server: { host: env.LUCID_FRONTEND_HOST || 'localhost', port: 5173, strictPort: true, https, proxy },
    preview: { host: 'localhost', port: 5173, strictPort: true, https, proxy },
  };
});
