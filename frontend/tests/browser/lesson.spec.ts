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

/** Answers the conversational starting check: "yes" to the experience question, then each probe. */
async function answerStartingCheck(page: import('@playwright/test').Page, answer: (question: string) => string) {
  for (let i = 0; i < 10; i++) {
    await expect(page.locator('#status')).toHaveText('Connected');
    const exercise = await page.locator('#reply').getAttribute('data-exercise');
    if (exercise !== 'experience' && exercise !== 'diagnostic') return;
    const text = (await page.locator('#beats').textContent()) ?? '';
    await page.locator('#reply-text').fill(exercise === 'experience' ? 'yes' : answer(text));
    await page.locator('#reply-send').click();
    await expect(page.locator('#status')).toHaveText('Connected');
  }
  throw new Error('The starting check did not finish');
}

/** Steps through a guided lesson until its last step is shown (lessons end without a check). */
async function finishAuthoredSteps(page: import('@playwright/test').Page) {
  for (let i = 0; i < 8; i++) {
    await expect(page.locator('#status')).toHaveText('Connected');
    const exercise = await page.locator('#reply').getAttribute('data-exercise');
    const [step, total] = ((await page.locator('#reply').getAttribute('data-step')) ?? '').split('/');
    if (!exercise && step === total) return;
    if (exercise === 'prediction_choice') {
      await page.locator('#reply-text').fill('A');
      await page.locator('#reply-send').click();
    } else await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await expect(page.locator('#status')).toHaveText('Connected');
  }
  throw new Error('The authored lesson did not reach its last step');
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
  await expect(page.locator('#shadow-story svg')).toBeVisible();
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
  await expect(page.getByRole('alert')).toContainText('ANTHROPIC_API_KEY');
  await page.getByLabel('Or explore any concept freely').fill('derivatives');
  await expect(page.locator('#status')).toHaveText('Connected');
  await page.getByRole('button', { name: 'Explore concept' }).click();
  await expect(page.locator('#scene-meta')).toContainText('revision 2');
  // The derivative lesson has its own local parameter controls.
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
  await page.getByRole('button', { name: 'Vector model', exact: true }).click();
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
  await page.getByLabel('Use memory to skip familiar prerequisites').check();
  await page.getByRole('button', { name: 'Start learning' }).click();
  await expect(page.locator('#beats')).toContainText('Have you worked with Dot product before?');
  await page.getByLabel('Your answer').fill('yes');
  await page.getByRole('button', { name: 'Answer', exact: true }).click();
  await expect(page.locator('#beats')).toContainText('dot (3, 4)');
  await page.getByLabel('Your answer').fill('11');
  await page.getByRole('button', { name: 'Answer', exact: true }).click();
  await expect(page.locator('#beats')).toContainText("You've got Dot product");
  // The default mode must teach the requested idea even after it is mastered.
  await page.getByLabel('Use memory to skip familiar prerequisites').uncheck();
  await page.getByRole('button', { name: 'Start learning' }).click();
  await expect(page.locator('#beats')).toContainText('Imagine shining parallel light');
  await expect(page.locator('#shadow-story')).toBeVisible();
  await page.getByRole('button', { name: 'Review', exact: true }).click();
  await expect(page.locator('#beats')).toContainText("Let's review Dot product");
  await expect(page.getByRole('slider', { name: 'Vector angle' })).toBeVisible();
  await page.getByRole('button', { name: 'Go deeper', exact: true }).click();
  await expect(page.locator('#beats')).toContainText("Let's go further with Dot product");
  // One note per turn: the step checks in, and its question comes after the learner replies.
  await expect(page.locator('#beats')).toContainText('Make sense so far?');
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(page.locator('#beats')).toContainText('points along which axis');
  await expect(page.getByRole('button', { name: 'Ask a question', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
});

test('shadow animation is local, pausable, typeset, and switches to the vector model', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByLabel('Private backend token').fill('browser-test-only-not-a-real-secret');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  await page.getByLabel('What do you want to learn?').fill('dot product');
  await page.getByRole('button', { name: 'Start learning' }).click();
  await expect(page.locator('#shadow-story .katex')).toBeVisible();
  const vector = page.locator('#story-vector');
  const equation = page.locator('#shadow-story .story-equation');
  const angle = page.getByRole('slider', { name: 'Vector angle' });
  let commands = 0;
  page.on('websocket', socket => socket.on('framesent', () => commands++));
  let requests = 0; page.on('request', request => { if (request.url().includes('/api/')) requests++; });
  // The first beat asks how the shadow changes as the arrow rotates, and plays exactly that.
  await expect.poll(() => angle.inputValue(), { timeout: 8000 }).toBe('90');
  await expect(equation).toHaveAttribute('data-equation', /\|s\|=0\.00/);
  await page.getByRole('button', { name: 'Reset parameters', exact: true }).click();
  const before = await vector.getAttribute('d');
  await page.getByRole('button', { name: 'Sweep freely', exact: true }).click();
  await expect.poll(() => vector.getAttribute('d')).not.toBe(before);
  await page.getByRole('button', { name: 'Pause sweep', exact: true }).click();
  const paused = await vector.getAttribute('d');
  await page.waitForTimeout(250);
  expect(await vector.getAttribute('d')).toBe(paused);
  await page.getByRole('button', { name: 'Reset parameters', exact: true }).click();
  expect(await vector.getAttribute('d')).toBe(before);
  // Selecting a beat replays its motion from the spoken example to the shadowless angle.
  expect(await angle.inputValue()).toBe('56.3');
  await page.locator('#beats button', { hasText: 'How long will an arrow' }).click();
  await expect.poll(() => angle.inputValue(), { timeout: 8000 }).toBe('90');
  expect(requests).toBe(0); expect(commands).toBe(0);
  await page.getByRole('button', { name: 'Vector model', exact: true }).click();
  await expect(page.locator('#viewport canvas')).toBeVisible();
  await expect(page.locator('#shadow-story')).toBeHidden();
  await page.getByRole('button', { name: 'Shadow story', exact: true }).click();
  await page.screenshot({ path: 'test-results/shadow-studio.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/shadow-mobile.png', fullPage: true });
  expect(errors).toEqual([]);
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
  await page.getByLabel('Use memory to skip familiar prerequisites').check();
  await page.getByRole('button', { name: 'Start learning' }).click();
  const beats = page.locator('#beats');
  // A conversational opening, then open questions: no options read out.
  await expect(beats).toContainText('Have you worked with Mean value theorem before?');
  // Answer the starting check: only the functions question correctly.
  await answerStartingCheck(page, text => text.includes('2x + 1') ? '7' : 'not sure');
  await expect(beats).toContainText("You know Functions, so let's start there.");
  await expect(beats).not.toContainText('route');
  await expect(beats.locator('button.beat')).toHaveCount(1);  // one note per turn
  await page.locator('#reply-text').fill('yes');  // "Make sense so far?" "Yes."
  await page.locator('#reply-send').click();
  await expect(beats).toContainText('Two points have the same height');
  // Every explanation has a diagram; its sliders re-plot locally.
  await page.waitForLoadState('networkidle');
  let requests = 0; page.on('request', request => { if (request.url().includes('/api/')) requests++; });
  const first = page.getByRole('slider', { name: 'first point' });
  await first.fill('-2.5'); await first.dispatchEvent('input');
  await expect(page.locator('#sliders output').first()).toHaveText('-2.50');
  expect(requests).toBe(0);
  await page.getByRole('button', { name: "I'm confused" }).click();
  await expect(beats).toContainText("Let's go through it again, slower.");
  await expect(page.locator('#status')).toHaveText('Connected');
  await finishAuthoredSteps(page);
  // No quick check at the end: Continue moves along the route.
  await expect(beats).not.toContainText('Quick check');
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(beats).toContainText('Now, Derivatives.');
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
  await answerStartingCheck(page, () => '5');
  await expect(page.locator('.review-topic').first()).toBeVisible();
  const before = await page.request.get('/api/me/reviews', { headers: { Authorization: 'Bearer browser-test-only-not-a-real-secret' } });
  await page.getByRole('button', { name: 'Review due topics', exact: true }).click();
  await expect(page.locator('#reply')).toHaveAttribute('data-exercise', 'recall_choice');
  await expect(page.locator('#beats')).toContainText("Let's see what you remember");
  await expect(page.locator('#scene-meta')).not.toContainText('0 objects');
  await page.getByRole('button', { name: 'Skip', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  const after = await page.request.get('/api/me/reviews', { headers: { Authorization: 'Bearer browser-test-only-not-a-real-secret' } });
  const scores = (value: { topics: { concept_id: string; mastery: number; reviews: number }[] }) => value.topics.map(t => [t.concept_id, t.mastery, t.reviews]).sort();
  expect(scores(await after.json())).toEqual(scores(await before.json()));
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
});

test('tutor actions carry what the learner sees: active step and moved sliders', async ({ page }) => {
  const sent: { type?: string; action?: string; view?: { active_beat: number | null; sliders: Record<string, number>; animating: string | null; in_vr: boolean } }[] = [];
  page.on('websocket', socket => socket.on('framesent', frame => {
    try { sent.push(JSON.parse(String(frame.payload))); } catch { /* auth frame only */ }
  }));
  await page.goto('/');
  await page.getByLabel('Private backend token').fill('browser-test-only-not-a-real-secret');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  await page.getByLabel('What do you want to learn?').fill('I want to learn the mean value theorem');
  await page.getByLabel('Use memory to skip familiar prerequisites').check();
  await page.getByRole('button', { name: 'Start learning' }).click();
  const beats = page.locator('#beats');
  // Earlier tests may already have taught this learner, so the check can be short or skipped.
  await expect(page.locator('#status')).toHaveText('Connected');
  await answerStartingCheck(page, text => text.includes('2x + 1') ? '7' : 'not sure');
  await expect(beats.locator('button.beat').first()).toBeVisible();
  const goal = sent.find(frame => frame.type === 'tutor' && frame.action === 'goal');
  expect(goal?.view).toEqual({ active_beat: null, sliders: {}, animating: null, in_vr: false });
  await page.locator('#beats button.beat').first().click();
  // Slope ('first point') and Derivatives ('point x') both bind param.p.
  const slider = page.locator('#sliders input[type=range]').first();
  await slider.fill('-2.5'); await slider.dispatchEvent('input');
  await page.getByRole('button', { name: "I'm confused" }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  const confused = sent.filter(frame => frame.type === 'tutor' && frame.action === 'confused').at(-1);
  expect(confused?.view?.active_beat).toBe(0);
  expect(confused?.view?.sliders['param.p']).toBe(-2.5);
  expect(confused?.view?.in_vr).toBe(false);
});
