import { test, expect } from '@playwright/test';

test('typed Realtime conversation reads the brain, publishes animated code and advances without a microphone', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const tools: string[] = [];
  page.on('request', request => {
    if (request.url().endsWith('/realtime-tools')) tools.push(request.postDataJSON().name);
  });
  // Only the external Realtime transport is simulated. Auth, graph lookup, compilation,
  // scene epochs and rendering use the real isolated backend and the production client.
  await page.route('**/api/health', async route => {
    const response = await route.fetch();
    await route.fulfill({ json: { ...await response.json(), voice: 'realtime', author: 'realtime', model: 'gpt-realtime-2.1' } });
  });
  await page.route('**/api/voice/sessions', route => route.fulfill({ json: {
    model: 'gpt-realtime-2.1', client_secret: 'ek_mock_only', sdp_url: 'https://api.openai.com/v1/realtime/calls',
  } }));
  await page.route('https://api.openai.com/v1/realtime/calls', route => route.fulfill({ body: 'mock-sdp' }));
  await page.addInitScript(() => {
    const state = window as unknown as { realtimeSent: any[]; microphoneCalls: number };
    state.realtimeSent = []; state.microphoneCalls = 0;
    navigator.mediaDevices.getUserMedia = async () => { state.microphoneCalls++; throw new Error('No microphone should be requested.'); };
    let channel: any;
    let action = '', stage = 0, sequence = 0, concept = '';
    const emit = (event: object) => channel.onmessage?.({ data: JSON.stringify(event) });
    const scene = {
      title: 'Why does a curve change direction?',
      diagram: { dimensions: 2, axes: true, sliders: [{ name: 'p', label: 'Position', min: -2, max: 2, value: 0 }],
        curves: [{ id: 'curve', x: 't', y: '0.25*t^2', t_min: -2, t_max: 2, style: 'primary' }],
        points: [{ id: 'point', x: 'p', y: '0.25*p^2', text: null }], arrows: [],
        labels: [{ id: 'formula', text: '$f(x)=\\frac{x^2}{4}$', x: 0, y: 4 }], graph: null },
      beats: [
        { say: 'Watch the point climb the curve. What changes as it moves?', focus: ['curve', 'point', 'formula'], animate: [{ param: 'p', to: 1, seconds: 1 }] },
        { say: 'The curve gets steeper as the point travels right.', focus: ['curve', 'point', 'formula'], animate: [{ param: 'p', to: 2, seconds: 1 }] },
      ],
    };
    class Peer {
      connectionState = 'connected'; localDescription = { sdp: 'mock' };
      addTransceiver() {}
      addTrack() {}
      createOffer() { return Promise.resolve({ sdp: 'mock' }); }
      setLocalDescription() { return Promise.resolve(); }
      setRemoteDescription() { setTimeout(() => channel.onopen?.(), 0); return Promise.resolve(); }
      close() {}
      createDataChannel() {
        channel = { readyState: 'open', close() {}, send(raw: string) {
          const event = JSON.parse(raw); state.realtimeSent.push(event);
          if (event.item?.role === 'user') { action = JSON.parse(event.item.content[0].text).action; stage = 0; }
          if (event.item?.type === 'function_call_output') {
            const result = JSON.parse(event.item.output);
            if (result.concept_id) concept = result.concept_id;
          }
          if (event.type !== 'response.create') return;
          setTimeout(() => {
            const id = `response_${++sequence}`;
            emit({ type: 'response.created', response: { id } });
            if (event.response.instructions) {
              emit({ type: 'response.output_text.delta', delta: event.response.instructions.split('\n').slice(1).join('\n') });
              emit({ type: 'response.done', response: { id, status: 'completed', output: [] } });
              return;
            }
            const name = stage++ === 0 ? 'get_learning_context' : action === 'continue' ? 'advance_step' : 'present_lesson';
            const args = name === 'get_learning_context' ? { topic: 'derivatives' }
              : name === 'advance_step' ? {} : { concept_id: concept, lesson: scene };
            emit({ type: 'response.done', response: { id, status: 'completed', output: [{ type: 'function_call',
              name, call_id: `call_${sequence}`, arguments: JSON.stringify(args) }] } });
          }, 0);
        } };
        return channel;
      }
    }
    window.RTCPeerConnection = Peer as unknown as typeof RTCPeerConnection;
  });
  await page.goto('/');
  await page.getByLabel('Private backend token').fill('browser-test-only-not-a-real-secret');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Connected');
  await page.getByLabel('What do you want to learn?').fill('derivatives');
  await page.getByRole('button', { name: 'Start learning' }).click();
  await expect(page.locator('#beats')).toContainText('Watch the point climb');
  await expect(page.locator('#realtime-response')).toContainText('Watch the point climb');
  await expect(page.locator('#math-panel .katex')).toBeVisible();
  await expect(page.getByRole('slider', { name: 'Position', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await expect(page.locator('#beats')).toContainText('curve gets steeper');
  await expect(page.locator('#scene-meta')).toContainText('revision 2');
  expect(tools).toContain('get_learning_context'); expect(tools).toContain('present_lesson'); expect(tools).toContain('advance_step');
  const state = await page.evaluate(() => {
    const state = window as unknown as { microphoneCalls: number; realtimeSent: any[] };
    return { mic: state.microphoneCalls, responses: state.realtimeSent.filter(e => e.type === 'response.create') };
  });
  expect(state.mic).toBe(0);
  expect(state.responses.every(e => e.response.output_modalities[0] === 'text')).toBe(true);
  await page.screenshot({ path: 'test-results/realtime-text-studio.png', fullPage: true });
  expect(errors).toEqual([]);
});
