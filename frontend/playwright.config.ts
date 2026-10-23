import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';
const python = resolve('../backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
export default defineConfig({
  testDir: './tests/browser', timeout: 30000, workers: 1,
  use: { baseURL: 'http://localhost:5173', headless: true, viewport: { width: 1440, height: 1000 } },
  webServer: [
    { command: `"${python}" tests/serve_backend.py`, url: 'http://127.0.0.1:8001/api/health', reuseExistingServer: false },
    { command: 'npm run dev', url: 'http://localhost:5173', env: { LUCID_BACKEND_URL: 'http://127.0.0.1:8001' }, reuseExistingServer: false },
  ],
});
