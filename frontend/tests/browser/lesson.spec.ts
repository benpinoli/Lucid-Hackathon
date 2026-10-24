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

async function finishAuthoredSteps(page: import('@playwright/test').Page) {
  for (let i = 0; i < 8; i++) {
    await expect(page.locator('#status')).toHaveText('Connected');
    const exercise = await page.locator('#reply').getAttribute('data-exercise');
    if (exercise === 'check') return;
    if (exercise === 'prediction_choice') {
      await page.locator('#reply-text').fill('A');
      await page.locator('#reply-send').click();
    } else await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await expect(page.locator('#status')).toHaveText('Connected');
  }
  throw new Error('The authored lesson did not reach its final retrieval check');
}

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
  await page.waitForLoadState('networkidle');
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
  await finishAuthoredSteps(page);
  await page.waitForTimeout(1200);
  await expect(page.locator('#metrics')).toContainText('textures');
  const baseline = (await page.locator('#metrics').textContent())!.match(/(\d+) geometries · (\d+) textures/)!.slice(1).map(Number);
  // At most 16 commands/sec including receipt ACKs; respect the backend's limit of 20.
  const startingEpoch = Number((await page.locator('#scene-meta').textContent())!.match(/Epoch (\d+)/)![1]);
  const startingRevision = Number((await page.locator('#scene-meta').textContent())!.match(/revision (\d+)/)![1]);
  await page.evaluate(async start => {
    const socket = (window as unknown as { sceneSocket: WebSocket }).sceneSocket;
    for (let epoch = start; epoch < start + 500; epoch++) {
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
  }, startingEpoch);
  await expect(page.locator('#scene-meta')).toContainText(`revision ${startingRevision + 500}`);
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

test('learner can review and go deeper after mastering a goal', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Private backend token').fill('browser-test-only-not-a-real-secret');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  await page.getByLabel('What do you want to learn?').fill('dot product');
  await page.getByRole('button', { name: 'Start learning' }).click();
  await expect(page.locator('#beats')).toContainText('dot (3, 4)');
  await page.getByLabel('Your answer').fill('11');
  await page.getByRole('button', { name: 'Answer', exact: true }).click();
  await expect(page.locator('#beats')).toContainText("You've got Dot product");
  await page.getByRole('button', { name: 'Review', exact: true }).click();
  await expect(page.locator('#beats')).toContainText("Let's review Dot product");
  await expect(page.getByRole('slider', { name: 'Vector angle' })).toBeVisible();
  await page.getByRole('button', { name: 'Go deeper', exact: true }).click();
  await expect(page.locator('#beats')).toContainText("Let's go further with Dot product");
  await expect(page.locator('#beats')).toContainText('Quick check:');
  await expect(page.getByRole('button', { name: 'Ask a question', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
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
    await finishAuthoredSteps(page);
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
  await expect(beats).toContainText('Two points have the same height');
  // Every explanation has a diagram; its sliders re-plot locally.
  await page.waitForLoadState('networkidle');
  let requests = 0; page.on('request', request => { if (request.url().includes('/api/')) requests++; });
  const first = page.getByRole('slider', { name: 'first point' });
  await first.fill('-2.5'); await first.dispatchEvent('input');
  await expect(page.locator('#sliders output').first()).toHaveText('-2.50');
  expect(requests).toBe(0);
  await page.getByRole('button', { name: "I'm confused" }).click();
  await expect(beats).toContainText("Let's slow down and go through it again.");
  await expect(page.locator('#status')).toHaveText('Connected');
  await finishAuthoredSteps(page);
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

test('true 3D complex-root model rotates and exposes model manipulation', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByLabel('Private backend token').fill('browser-test-only-not-a-real-secret');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  await page.getByLabel('Or explore any concept freely').fill('3D graph of a quadratic complex roots');
  await page.getByRole('button', { name: 'Explore concept' }).click();
  await expect(page.locator('#beats')).toContainText('both the real and imaginary');
  await expect(page.locator('#scene-meta')).toContainText('7 objects');
  const viewport = page.locator('#viewport');
  const before = await viewport.screenshot();
  const bounds = (await page.locator('canvas').boundingBox())!;
  await page.mouse.move(bounds.x + bounds.width * 0.6, bounds.y + bounds.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width * 0.8, bounds.y + bounds.height * 0.6, { steps: 15 });
  await page.mouse.up(); await page.waitForTimeout(500);
  expect((await viewport.screenshot()).equals(before)).toBe(false);
  await page.getByLabel('Interaction', { exact: true }).selectOption('rotate');
  await expect(page.getByLabel('Whole model')).toBeChecked();
  await page.getByRole('button', { name: 'Reset arrangement', exact: true }).click();
  await viewport.screenshot({ path: 'test-results/complex-roots-3d.png' });
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  expect(errors).toEqual([]);
});

test('learning day offers optional retrieval without showing the previous explanation', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Private backend token').fill('browser-test-only-not-a-real-secret');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  await expect(page.getByRole('button', { name: 'Learn something new', exact: true })).toBeVisible();
  // Establish an assessment in this session so the test also works in isolation.
  await page.getByLabel('What do you want to learn?').fill('vector length');
  await page.getByRole('button', { name: 'Start learning' }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  if ((await page.locator('#reply').getAttribute('data-exercise')) === 'diagnostic') {
    await page.locator('#reply-text').fill('5'); await page.locator('#reply-send').click();
    await expect(page.locator('#status')).toHaveText('Connected');
  }
  await expect(page.locator('.review-topic').first()).toBeVisible();
  const before = await page.request.get('/api/me/reviews', { headers: { Authorization: 'Bearer browser-test-only-not-a-real-secret' } });
  await page.getByRole('button', { name: 'Review due topics', exact: true }).click();
  await expect(page.locator('#reply')).toHaveAttribute('data-exercise', 'recall_choice');
  await expect(page.locator('#beats')).toContainText('retrieve before looking');
  await expect(page.locator('#scene-meta')).not.toContainText('0 objects');
  await page.getByRole('button', { name: 'Skip', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  const after = await page.request.get('/api/me/reviews', { headers: { Authorization: 'Bearer browser-test-only-not-a-real-secret' } });
  const scores = (value: { topics: { concept_id: string; mastery: number; reviews: number }[] }) => value.topics.map(t => [t.concept_id, t.mastery, t.reviews]).sort();
  expect(scores(await after.json())).toEqual(scores(await before.json()));
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
});
