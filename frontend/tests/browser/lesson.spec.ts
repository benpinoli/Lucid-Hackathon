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
  await expect(page.locator('#sliders output')).toHaveText('90.0°');
  expect(requests).toBe(0);
  // Close the real transport; client must recover the same retained lesson.
  await page.evaluate(() => (window as unknown as { sceneSocket: WebSocket }).sceneSocket.close());
  await expect(page.locator('#status')).not.toHaveText('Connected');
  await expect(page.locator('#status')).toHaveText('Connected', { timeout: 25000 });
  await expect(page.locator('#scene-meta')).toContainText('revision 1');
  await page.getByRole('button', { name: 'Interrupt', exact: true }).click();
  await expect(page.locator('#scene-meta')).toContainText('Epoch 2');
  await expect(page.locator('#scene-meta')).toContainText('6 objects');
  await page.getByLabel('Or explore any concept freely').fill('A topic without a recipe');
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

test('Dijkstra and mean value theorem lessons render without local sliders', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByLabel('Private backend token').fill('browser-test-only-not-a-real-secret');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  // The isolated test backend has no provider key: voice degrades to text mode.
  await expect(page.getByRole('button', { name: 'Start voice' })).toBeDisabled();
  await expect(page.locator('#voice-status')).toContainText('no OPENAI_API_KEY');
  const lessons = [
    { question: "Explain Dijkstra's algorithm", beat: 'A → C → B → D → E', objects: '25 objects', legend: 'shortest path', file: 'dijkstra' },
    { question: 'Explain the mean value theorem', beat: 'parallel to the secant', objects: '10 objects', legend: 'secant', file: 'mean-value' },
  ];
  for (const lesson of lessons) {
    await page.getByLabel('Or explore any concept freely').fill(lesson.question);
    await page.getByRole('button', { name: 'Explore concept' }).click();
    await expect(page.locator('#beats')).toContainText(lesson.beat);
    await expect(page.locator('#scene-meta')).toContainText(lesson.objects);
    await expect(page.locator('#legend')).toContainText(lesson.legend);
    await expect(page.locator('#sliders').getByRole('slider')).toHaveCount(0);
    await page.waitForTimeout(300);
    await page.locator('#viewport').screenshot({ path: `test-results/${lesson.file}.png` });
  }
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  expect(errors).toEqual([]);
});

test('tutor: spoken-style check, silent route, interactive diagrams, confusion and graded progress', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByLabel('Private backend token').fill('browser-test-only-not-a-real-secret');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  await page.getByLabel('What do you want to learn?').fill('I want to learn the mean value theorem');
  await page.getByRole('button', { name: 'Start learning' }).click();
  await expect(page.locator('#beats')).toContainText('Is it parallel to the secant');
  const beats = page.locator('#beats');
  // Answer the starting check: only the functions question correctly.
  for (let i = 0; i < 8 && !(await beats.textContent())?.includes("Next, let's look at"); i++) {
    await expect(page.locator('#status')).toHaveText('Connected');
    const text = (await beats.textContent()) ?? '';
    await page.getByLabel('Your answer').fill(text.includes('2x + 1') ? '7' : 'not sure');
    await page.getByRole('button', { name: 'Answer' }).click();
    await expect(page.locator('#status')).toHaveText('Connected');
  }
  await expect(beats).toContainText("Next, let's look at Slope and secant lines.");
  await expect(beats).not.toContainText('route');
  await expect(beats).toContainText('Quick check: What is the slope from (1, 2) to (3, 8)?');
  // Every explanation has a diagram; its sliders re-plot locally.
  let requests = 0; page.on('request', request => { if (request.url().includes('/api/')) requests++; });
  const first = page.getByRole('slider', { name: 'first point' });
  await first.fill('-2.5'); await first.dispatchEvent('input');
  await expect(page.locator('#sliders output').first()).toHaveText('-2.50');
  expect(requests).toBe(0);
  await page.getByRole('button', { name: "I'm confused" }).click();
  await expect(beats).toContainText("Let's slow down and go through it again.");
  await expect(page.locator('#status')).toHaveText('Connected');
  await page.getByLabel('Your answer').fill('3');
  await page.getByRole('button', { name: 'Answer' }).click();
  await expect(beats).toContainText("That's right.");
  await expect(beats).toContainText("Next, let's look at Derivatives.");
  await expect(page.getByRole('slider', { name: 'point x' })).toBeVisible();
  await page.waitForTimeout(300);
  await page.locator('#viewport').screenshot({ path: 'test-results/tutor-derivatives.png' });
  await page.locator('aside').screenshot({ path: 'test-results/tutor-panel.png' });
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  expect(errors).toEqual([]);
});
