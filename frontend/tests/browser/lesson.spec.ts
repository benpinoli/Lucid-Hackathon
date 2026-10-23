import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const Original = window.WebSocket;
    window.WebSocket = class extends Original {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols);
        (window as unknown as { sceneSocket: WebSocket }).sceneSocket = this;
      }
    };
  });
});

test('real backend: auth, lesson, local slider, reconnect, interruption and cleanup', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByLabel('Private backend token').fill('incorrect-token');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('401');
  await page.getByLabel('Private backend token').fill('browser-test-only-not-a-real-secret');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  await page.getByRole('button', { name: 'Explore concept' }).click();
  await expect(page.locator('#beats')).toContainText('The two arrows');
  await expect(page.locator('#scene-meta')).toContainText('revision 1');
  await expect(page.locator('canvas')).toBeVisible();
  await page.screenshot({ path: 'test-results/desktop.png', fullPage: true });
  let requests = 0; page.on('request', request => { if (request.url().includes('/api/')) requests++; });
  const slider = page.getByRole('slider', { name: 'Vector angle' });
  await slider.fill('90'); await slider.dispatchEvent('input');
  await expect(page.locator('output')).toHaveText('90.0°');
  expect(requests).toBe(0);
  // Close the real transport; client must recover the same retained lesson.
  await page.evaluate(() => (window as unknown as { sceneSocket: WebSocket }).sceneSocket.close());
  await expect(page.locator('#status')).not.toHaveText('Connected');
  await expect(page.locator('#status')).toHaveText('Connected', { timeout: 25000 });
  await expect(page.locator('#scene-meta')).toContainText('revision 1');
  await page.getByRole('button', { name: 'Interrupt', exact: true }).click();
  await expect(page.locator('#scene-meta')).toContainText('Epoch 2');
  await expect(page.locator('#scene-meta')).toContainText('6 objects');
  await page.getByLabel('What would you like to explore?').fill('A topic without a recipe');
  await page.getByRole('button', { name: 'Explore concept' }).click();
  await expect(page.locator('#beats')).toContainText('Try asking');
  await expect(slider).toHaveCount(0);
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Disconnected');
  expect(errors).toEqual([]);
});

test('500 rendered replacements retain bounded GPU resources', async ({ page }) => {
  test.setTimeout(300000);
  await page.goto('/');
  await page.getByLabel('Private backend token').fill('browser-test-only-not-a-real-secret');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  await page.getByRole('button', { name: 'Explore concept' }).click();
  await expect(page.locator('#scene-meta')).toContainText('revision 1');
  await expect(page.locator('#metrics')).toContainText('textures');
  const baseline = (await page.locator('#metrics').textContent())!.match(/(\d+) geometries · (\d+) textures/)!.slice(1).map(Number);
  // At most 16 commands/sec including receipt ACKs; respect the backend's limit of 20.
  await page.evaluate(async () => {
    const socket = (window as unknown as { sceneSocket: WebSocket }).sceneSocket;
    for (let epoch = 1; epoch <= 500; epoch++) {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Replacement response timeout')), 5000);
        const receive = (message: MessageEvent) => {
          const data = JSON.parse(message.data);
          if (data.type === 'lesson.turn') { clearTimeout(timer); socket.removeEventListener('message', receive); resolve(); }
          if (data.type === 'error') { clearTimeout(timer); reject(new Error('Backend rejected stress turn')); }
        };
        socket.addEventListener('message', receive);
        socket.send(JSON.stringify({ type: 'turn', text: 'Explain dot product', expected_epoch: epoch }));
      });
      await new Promise(resolve => setTimeout(resolve, 130));
    }
  });
  await expect(page.locator('#scene-meta')).toContainText('revision 501');
  await expect(page.locator('#scene-meta')).toContainText('6 objects');
  await page.waitForTimeout(1200);
  const after = (await page.locator('#metrics').textContent())!.match(/(\d+) geometries · (\d+) textures/)!.slice(1).map(Number);
  expect(after).toEqual(baseline);
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
});

test('narrow desktop layout stays within the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
