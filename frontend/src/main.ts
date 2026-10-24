import './style.css';
import { LessonClient } from './client';
import { LessonRenderer } from './renderer';
import { localProjection, type Plan, type Snapshot, type TutorState } from './protocol';
import { connectVoice, VoiceNarrator, type VoiceConnection } from './voice';

document.querySelector<HTMLDivElement>('#app')!.innerHTML = `
  <header><a class="brand" href="/">LUCID<span>LEARNING IN SPACE</span></a><span class="mode">DESKTOP / WEBXR</span><span id="status" role="status">Disconnected</span></header>
  <main>
    <aside>
      <div class="eyebrow">YOUR LEARNING STUDIO</div><h1>Make room for<br>understanding.</h1>
      <p class="intro">Explore ideas in a space you can move through. Try the dot product, Dijkstra's algorithm, or the mean value theorem.</p>
      <form id="connection"><label for="token">Private backend token</label><input id="token" type="password" autocomplete="off" placeholder="Paste the demo token" required /><div class="row"><button id="connect">Connect</button><button id="disconnect" type="button" class="quiet" disabled>Disconnect</button></div><p class="hint">Held in memory for this session only.</p></form>
      <section class="tutor" aria-label="Tutor">
        <form id="goal"><label for="goal-text">What do you want to learn?</label><input id="goal-text" type="text" maxlength="500" placeholder="e.g. the mean value theorem" required /><div class="row"><button id="goal-start" disabled>Start learning</button></div></form>
        <form id="reply" hidden><label for="reply-text" id="reply-label">Your answer</label><input id="reply-text" type="text" maxlength="2000" autocomplete="off" /><div class="row wrap"><button id="reply-send">Answer</button><button id="confused" type="button" class="quiet">I'm confused</button><button id="continue" type="button" class="quiet">Continue</button></div><p class="hint">With voice on, just talk: answer, ask, or say what's unclear.</p></form>
      </section>
      <form id="turn"><label for="question">Or explore any concept freely</label><textarea id="question" maxlength="2000" rows="3">Explain dot product</textarea><div class="row"><button id="send" disabled>Explore concept <span>↗</span></button><button id="interrupt" type="button" class="quiet" disabled>Interrupt</button></div></form>
      <section class="voice" aria-label="Voice tutor"><label>Voice tutor</label><div class="row"><button id="voice" type="button" class="quiet" disabled>Start voice</button><button id="mute" type="button" class="quiet" disabled aria-pressed="false">Mute mic</button></div><label class="check"><input id="barge-in" type="checkbox" /> Interrupt by talking (use headphones)</label><p class="hint">Otherwise the mic opens when the tutor finishes. To cut in, press Interrupt or pull the VR trigger.</p><p id="voice-status" class="hint" role="status">Connect first. Voice also needs OPENAI_API_KEY on the backend.</p></section>
      <div id="error" role="alert" hidden></div>
      <section class="lesson"><div class="eyebrow">GUIDED EXPLANATION</div><div id="beats"><p class="hint">Connect and explore a concept to begin.</p></div></section>
      <p class="footnote">Authored lessons · Text or voice<br>Voice reads the authored steps aloud</p>
    </aside>
    <section class="workspace" aria-label="Lesson workspace"><div class="toolbar"><div><span class="live-dot"></span><strong>Concept space</strong><span id="scene-meta">Awaiting lesson</span></div><div class="row"><button id="reset" class="quiet">Reset view</button><div id="xr"></div></div></div>
      <div id="viewport"><div id="empty"><span>01 / VECTOR LAB</span><h2>A new perspective<br>starts here.</h2><p>Connect your backend to bring the lesson into view.</p></div></div>
      <div class="scene-bottom"><div class="legend" id="legend"><span>● u / projection</span><span>● v</span></div><span>Drag to orbit · Scroll to zoom</span></div>
      <section id="sliders" aria-label="Local scene controls"></section>
      <div class="diagnostics"><span id="metrics">Renderer starting…</span><span>Scene schema v1</span></div>
      <p class="xr-note">VR: connect and load a lesson first. Use Enter VR on a supported device. Point a controller at ± angle and press the trigger. Desktop controls remain available outside VR.</p>
    </section>
  </main>`;

function element<T extends HTMLElement>(id: string) { return document.getElementById(id)! as T; }
const error = (text: string) => { element('error').textContent = text; element('error').hidden = false; };
const clearError = () => { element('error').hidden = true; };
let renderer: LessonRenderer | undefined;
try { renderer = new LessonRenderer(element('viewport'), element('xr'), text => { element('metrics').textContent = text; }, error); }
catch { error('WebGL is unavailable. Enable browser hardware acceleration and reload. Backend controls and lesson text are still usable.'); element('metrics').textContent = 'Renderer unavailable'; }
let snapshot: Snapshot | undefined;
let plan: Plan | undefined;
const overrides = new Map<string, number>();
function draw() {
  if (!snapshot) return;
  let nodes = snapshot.nodes;
  const params: Record<string, number> = {};
  for (const node of nodes) {
    const c = node.content;
    if (c.primitive === 'slider' && c.binding.startsWith('param.')) params[c.binding.slice(6)] = overrides.get(c.binding) ?? c.value;
  }
  const angleSlider = nodes.find(n => n.visible && n.content.primitive === 'slider' && n.content.binding === 'projection.angle');
  if (plan?.recipe_id === 'vector_projection_v1' && angleSlider?.content.primitive === 'slider') {
    const angle = overrides.get('projection.angle');
    const length = overrides.get('projection.length');
    if (angle !== undefined || length !== undefined) nodes = localProjection(nodes, angle ?? angleSlider.content.value, length);
  }
  renderer?.render(nodes, params);
}
/** Smallest 1-2-5 step at least `raw`, so slider values read cleanly (0.05, not 0.03). */
function niceStep(raw: number) {
  const power = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map(m => m * power).find(step => step >= raw - 1e-12)!;
}
function renderSliders() {
  const root = element('sliders'); root.replaceChildren(); renderer?.setXRSlider();
  if (!snapshot) return;
  let xrBound = false;
  for (const node of snapshot.nodes) {
    const c = node.content; if (!node.visible || c.primitive !== 'slider') continue;
    const projection = c.binding.startsWith('projection.');
    if (projection && plan?.recipe_id !== 'vector_projection_v1') continue;
    const name = projection ? (c.binding === 'projection.angle' ? 'Vector angle' : 'Vector length') : (c.label ?? c.binding.slice(6));
    const unit = c.binding === 'projection.angle' ? '°' : '';
    const step = projection ? 0.1 : niceStep((c.maximum - c.minimum) / 200);
    const label = document.createElement('label'); label.textContent = name;
    const output = document.createElement('output'); output.textContent = `${c.value.toFixed(projection ? 1 : 2)}${unit}`;
    const input = document.createElement('input'); input.type = 'range'; input.min = String(c.minimum); input.max = String(c.maximum); input.step = String(step); input.value = String(c.value); input.setAttribute('aria-label', name);
    const update = (value: number) => {
      const bounded = Math.min(c.maximum, Math.max(c.minimum, value));
      overrides.set(c.binding, bounded); input.value = String(bounded); output.textContent = `${bounded.toFixed(projection ? 1 : 2)}${unit}`;
      renderer?.setFocus([]); renderer?.setCaption('Local exploration · reset the lesson with a new turn.');
      draw();
    };
    input.addEventListener('input', () => update(Number(input.value)));
    label.append(output, input); root.append(label);
    if (!xrBound) { xrBound = true; renderer?.setXRSlider(direction => update(Number(input.value) + direction * (projection ? 5 : (c.maximum - c.minimum) / 20)), projection ? 'angle' : name); }
  }
  if (root.childElementCount) { const hint = document.createElement('p'); hint.className = 'hint'; hint.textContent = 'Explore locally · sliders update this view without network calls. Teaching text describes the original scene.'; root.append(hint); }
}
// Primary (teal) and secondary (orange) style meanings per authored recipe.
const legends: Record<string, string[]> = {
  vector_projection_v1: ['u / projection', 'v'],
  dijkstra_v1: ['nodes', 'shortest path'],
  learning_route_v1: ['known', 'your route'],
  mean_value_v1: ['f(x)', 'secant ∥ tangent'],
};
function showBeats(value?: Plan) {
  plan = value; const root = element('beats'); root.replaceChildren();
  if (value) element('legend').replaceChildren(...(legends[value.recipe_id] ?? []).map(text => Object.assign(document.createElement('span'), { textContent: `● ${text}` })));
  renderer?.setFocus([]); renderer?.setCaption('');
  if (!value) { root.textContent = 'Send a new turn to receive the explanation.'; return; }
  renderer?.setMarker(value.generated ? 'AI-generated' : undefined);
  if (value.planned_by === 'claude' && !value.generated) { const note = document.createElement('p'); note.className = 'hint'; note.textContent = 'Example chosen by Claude · every number computed and checked by LUCID.'; root.append(note); }
  for (const [index, beat] of value.beats.entries()) {
    const button = document.createElement('button'); button.className = 'beat';
    const number = document.createElement('span'); number.textContent = String(index + 1).padStart(2, '0');
    const text = document.createElement('span'); text.textContent = beat.say_text;
    button.append(number, text); button.onclick = () => { selectBeat(index); if (voice) narrator.speakBeat(value, index); };
    root.append(button);
  }
  // Full scene initially visible; focus is an explicit reader action.
  renderer?.setCaption(value.beats[0]?.say_text ?? '');
}
function selectBeat(index: number) {
  const beat = plan?.beats[index]; if (!beat) return;
  element('beats').querySelectorAll('button').forEach((button, i) => { if (i === index) button.setAttribute('aria-pressed', 'true'); else button.removeAttribute('aria-pressed'); });
  renderer?.setFocus(beat.focus_ids); renderer?.setCaption(beat.say_text);
}
let voice: VoiceConnection | undefined;
let muted = false;
let micOpen = true; // Turn-taking gate from the narrator; the learner's mute always wins.
const applyMic = () => voice?.setMuted(muted || !micOpen);
let sessionOpen = false;
let pendingVoiceTurn: { resolve: (plan: Plan) => void; reject: (error: Error) => void } | undefined;
const voiceStatus = (text: string) => { element('voice-status').textContent = text; };
const narrator = new VoiceNarrator({
  send: event => voice?.send(event),
  tutor: (action, text) => new Promise<Plan>((resolve, reject) => {
    pendingVoiceTurn?.reject(new Error('Superseded by a newer request'));
    client.command('tutor', text, { action });
    pendingVoiceTurn = { resolve, reject };
  }),
  focusBeat: selectBeat,
  status: voiceStatus,
  setMicOpen: open => { micOpen = open; applyMic(); },
});
function updateVoiceControls() {
  const button = element<HTMLButtonElement>('voice'), mute = element<HTMLButtonElement>('mute');
  button.textContent = voice ? 'Stop voice' : 'Start voice';
  button.disabled = !voice && !(sessionOpen && client.voiceAvailable);
  mute.disabled = !voice; mute.textContent = muted ? 'Unmute mic' : 'Mute mic'; mute.setAttribute('aria-pressed', String(muted));
}
function stopVoice(message = 'Voice off. Text controls still work.') {
  const previous = voice; voice = undefined; narrator.stop(); previous?.close();
  if (previous) voiceStatus(message);
  updateVoiceControls();
}
const client = new LessonClient({
  status: (text, ready) => {
    if (ready) sessionOpen = true; else if (text === 'Disconnected') { sessionOpen = false; stopVoice(); }
    updateVoiceControls();
    element('status').textContent = text; element('status').classList.toggle('connected', ready);
    element<HTMLButtonElement>('send').disabled = !ready; element<HTMLButtonElement>('interrupt').disabled = !ready;
  },
  state: value => {
    if (snapshot && (value.lesson_id !== snapshot.lesson_id || value.turn_epoch !== snapshot.turn_epoch)) showBeats();
    snapshot = value; overrides.clear(); element('empty').hidden = value.nodes.length > 0;
    element('scene-meta').textContent = `Epoch ${value.turn_epoch} · revision ${value.scene_revision} · ${value.nodes.length} objects`;
    draw(); renderSliders();
  },
  plan: value => {
    showBeats(value); renderSliders(); showTutor(value);
    if (pendingVoiceTurn) { pendingVoiceTurn.resolve(value); pendingVoiceTurn = undefined; }
    else if (voice) narrator.introduce(value);
  },
  error: text => {
    error(text);
    pendingVoiceTurn?.reject(new Error(text)); pendingVoiceTurn = undefined;
  },
});
element('connection').addEventListener('submit', async event => {
  event.preventDefault(); clearError(); element<HTMLButtonElement>('connect').disabled = true;
  try {
    await client.connect(element<HTMLInputElement>('token').value); element<HTMLInputElement>('token').value = ''; element<HTMLButtonElement>('disconnect').disabled = false;
    element<HTMLButtonElement>('goal-start').disabled = false;
    voiceStatus(client.voiceAvailable ? 'Voice is available. Start voice, then ask about a topic out loud.' : 'Voice is off: the backend has no OPENAI_API_KEY. Text lessons work normally.');
    updateVoiceControls();
  }
  catch (e) { error(e instanceof Error ? e.message : 'Connection failed'); }
  finally { element<HTMLButtonElement>('connect').disabled = false; }
});
element('disconnect').onclick = async () => {
  await client.disconnect(); snapshot = undefined; showBeats(); overrides.clear(); renderer?.render([]); renderer?.setXRSlider();
  element<HTMLButtonElement>('goal-start').disabled = true; element('reply').hidden = true; tutorState = null;
  element('sliders').replaceChildren(); element('empty').hidden = false; element('scene-meta').textContent = 'Awaiting lesson'; element<HTMLButtonElement>('disconnect').disabled = true;
};
element('turn').addEventListener('submit', event => { event.preventDefault(); clearError(); try { client.command('turn', element<HTMLTextAreaElement>('question').value); } catch (e) { error((e as Error).message); } });
// ---- tutor: the learner talks or types; the route and scores stay on the server ----
let tutorState: TutorState | null = null;
function tutor(action: string, text = '') {
  clearError();
  try { client.command('tutor', text, { action }); } catch (e) { error((e as Error).message); }
}
function showTutor(value: Plan) {
  if (!value.tutor) return;
  tutorState = value.tutor;
  const awaiting = value.tutor.awaiting_answer;
  element('reply').hidden = false;
  element('reply-label').textContent = awaiting ? 'Your answer' : 'Ask the tutor anything';
  element('reply-send').textContent = awaiting ? 'Answer' : 'Ask';
}
element('goal').addEventListener('submit', event => { event.preventDefault(); tutor('goal', element<HTMLInputElement>('goal-text').value); });
element('reply').addEventListener('submit', event => {
  event.preventDefault();
  const input = element<HTMLInputElement>('reply-text'); const text = input.value;
  if (!text.trim() && !tutorState?.awaiting_answer) return;
  tutor(tutorState?.awaiting_answer ? 'answer' : 'question', text); input.value = '';
});
element('confused').onclick = () => { const input = element<HTMLInputElement>('reply-text'); tutor('confused', input.value || "I don't understand this part."); input.value = ''; };
element('continue').onclick = () => tutor('continue');
element('voice').onclick = async () => {
  if (voice) { stopVoice(); return; }
  clearError(); element<HTMLButtonElement>('voice').disabled = true; voiceStatus('Connecting voice… allow microphone access if asked.');
  try {
    const credential = await client.voiceCredential();
    const connection: VoiceConnection = await connectVoice(credential, event => narrator.handle(event), () => {
      if (voice === connection) stopVoice('Voice disconnected. Text controls still work; start voice again to retry.');
    });
    voice = connection; muted = false; applyMic();
    voiceStatus('Listening… try “explain Dijkstra’s algorithm”.');
  } catch (e) { voiceStatus(e instanceof Error ? e.message : 'Voice failed to start.'); }
  finally { updateVoiceControls(); }
};
element('mute').onclick = () => { muted = !muted; applyMic(); voiceStatus(muted ? 'Microphone muted.' : 'Listening…'); updateVoiceControls(); };
element<HTMLInputElement>('barge-in').onchange = event => { narrator.bargeIn = (event.target as HTMLInputElement).checked; };
// VR: a trigger pull that misses the slider buttons cuts the tutor off and opens the mic.
renderer?.setXRInterrupt(() => narrator.stop());
element('interrupt').onclick = () => { clearError(); narrator.stop(); try { client.command('interrupt'); showBeats(); } catch (e) { error((e as Error).message); } };
element('reset').onclick = () => renderer?.resetView();
window.addEventListener('pagehide', () => { stopVoice(); void client.disconnect(); renderer?.dispose(); });
