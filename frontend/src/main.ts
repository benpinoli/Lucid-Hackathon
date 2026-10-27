import './style.css';
import { typeset } from './math';
import { ShadowStory } from './shadow';
import { LessonClient } from './client';
import { LessonRenderer } from './renderer';
import { ModelCache, type ModelInfo } from './models';
import { localProjection, type Beat, type LearnerView, type Plan, type Snapshot, type TutorState } from './protocol';
import { connectVoice, VoiceNarrator, type VoiceConnection } from './voice';

document.querySelector<HTMLDivElement>('#app')!.innerHTML = `
  <header><a class="brand" href="/">LUCID<span>LEARNING IN SPACE</span></a><span class="mode">DESKTOP / WEBXR</span><span id="status" role="status">Disconnected</span></header>
  <main>
    <aside>
      <div class="eyebrow">YOUR LEARNING STUDIO</div><h1>Make room for<br>understanding.</h1>
      <p class="intro">See the problem. Move the picture. Discover the mathematics. Try dot product, derivatives, or a topic of your own.</p>
      <form id="connection"><label for="token">Private backend token</label><input id="token" type="password" autocomplete="off" placeholder="Paste the demo token" required /><div class="row"><button id="connect">Connect</button><button id="disconnect" type="button" class="quiet" disabled>Disconnect</button></div><p class="hint">Held in memory for this session only.</p></form>
      <section class="tutor" aria-label="Tutor">
        <section id="learning-day" hidden aria-label="Your learning day"><label>Your learning day</label><p class="hint">Learn something new, or start with a short retrieval warm-up.</p><div class="row wrap"><button id="learn-new" type="button" class="quiet">Learn something new</button><button id="warmup" type="button">Review due topics</button></div><p id="review-status" class="hint" role="status"></p><div id="review-topics"></div></section>
      <form id="goal"><label for="goal-text">What do you want to learn?</label><input id="goal-text" type="text" maxlength="500" placeholder="e.g. Fourier transforms" required /><label class="check"><input id="adaptive-route" type="checkbox" /> Use memory to skip familiar prerequisites</label><div class="row"><button id="goal-start" disabled>Start learning</button></div><p class="hint">Start from the beginning by default. Your saved knowledge stays intact.</p><p id="topic-status" class="hint" role="status"></p></form>
        <form id="reply" hidden><label for="reply-text" id="reply-label">Your answer</label><input id="reply-text" type="text" maxlength="2000" autocomplete="off" /><div class="row wrap"><button id="reply-send">Answer</button><button id="confused" type="button" class="quiet">I'm confused</button><button id="hint" type="button" class="quiet">Hint</button><button id="continue" type="button" class="quiet">Continue</button></div><p class="hint">With voice on, just talk: answer, ask, or say what's unclear.</p></form>
      </section>
      <form id="turn"><label for="question">Or explore any concept freely</label><textarea id="question" maxlength="2000" rows="3">Explain dot product</textarea><div class="row"><button id="send" disabled>Explore concept <span>↗</span></button><button id="interrupt" type="button" class="quiet" disabled>Interrupt</button></div></form>
      <section class="voice" aria-label="Voice tutor"><label>Voice tutor</label><div class="row"><button id="voice" type="button" class="quiet" disabled>Start voice</button><button id="mute" type="button" class="quiet" disabled aria-pressed="false">Mute mic</button></div><label class="check"><input id="barge-in" type="checkbox" /> Interrupt by talking (use headphones)</label><p class="hint">Otherwise the mic opens when the tutor finishes. To cut in, press Interrupt or pull the VR trigger.</p><p id="voice-status" class="hint" role="status">Connect first. Voice also needs OPENAI_API_KEY on the backend.</p></section>
      <div id="error" role="alert" hidden></div>
      <section class="lesson"><div class="eyebrow">GUIDED EXPLANATION</div><div id="beats"><p class="hint">Connect and explore a concept to begin.</p></div></section>
      <p class="footnote">Active lessons · Text or voice<br>Predict, explain, connect, and recall</p>
    </aside>
    <section class="workspace" aria-label="Lesson workspace"><div class="toolbar"><div><span class="live-dot"></span><strong>Concept space</strong><span id="scene-meta">Awaiting lesson</span></div><div class="row"><button id="reset" class="quiet">Reset view</button><div id="xr"></div></div></div>
      <div id="projection-views" class="row" hidden><button id="view-story" class="quiet" type="button" aria-pressed="true">Shadow story</button><button id="view-model" class="quiet" type="button" aria-pressed="false">Vector model</button></div>
      <section id="shadow-story" hidden aria-label="Shadow thought experiment"></section>
      <div id="viewport"><div id="empty"><span>01 / VECTOR LAB</span><h2>A new perspective<br>starts here.</h2><p>Connect your backend to bring the lesson into view.</p></div></div>
      <section id="math-panel" aria-label="Lesson equations" hidden></section>
      <p id="scene-caption" class="scene-caption" aria-live="polite"></p>
      <div id="playback" class="row wrap" hidden><button id="play-animation" type="button">Sweep freely</button><button id="restart-animation" type="button" class="quiet">Reset parameters</button><label for="animation-parameter">Animate</label><select id="animation-parameter"></select><label for="animation-speed">Speed</label><select id="animation-speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option></select></div>
      <div class="row wrap" aria-label="Scene manipulation"><label for="manipulation">Interaction</label><select id="manipulation"><option value="orbit">Orbit camera</option><option value="translate">Move model</option><option value="rotate">Rotate model</option></select><label class="check"><input id="whole-model" type="checkbox" checked /> Whole model</label><button id="reset-arrangement" type="button" class="quiet">Reset arrangement</button></div>
      <div class="scene-bottom"><div class="legend" id="legend"><span>● u / projection</span><span>● v</span></div><span>Drag to orbit · Scroll to zoom</span></div>
      <p id="model-credits" class="hint" hidden></p>
      <section id="sliders" aria-label="Local scene controls"></section>
      <div class="diagnostics"><span id="metrics">Renderer starting…</span><span>Scene schema v1</span></div>
      <p class="xr-note">VR: point at the model and squeeze the grip to grab, move, and rotate it; release to place it. Hand-tracked pinch also grabs when supported. Whole model keeps the diagram together; uncheck it to move individual objects. Use the trigger for slider buttons. Desktop: choose Move or Rotate for handles, or Orbit to look around.</p>
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
let projectionModel = false;
const story = new ShadowStory(element('shadow-story'));
let playing = false;
let activeBeat: number | null = null; // The step the learner selected or the narrator is on.
let animationTime = 0;
let animationFrame = 0;
let animationPhase = 0;
let animationBinding = '';
let beatFrame = 0;
// fromBeat: a narrated animation keeps the beat's highlight; a learner's drag clears it.
const parameterUpdates = new Map<string, (value: number, fromBeat?: boolean) => void>();
function pauseAnimation() {
  playing = false; cancelAnimationFrame(animationFrame); cancelAnimationFrame(beatFrame);
  element('play-animation').textContent = 'Sweep freely';
}
/** Plays the slider moves a beat describes, eased, while that beat is spoken or selected. */
function playBeat(beat?: Beat) {
  const moves = (beat?.animations ?? []).filter(move => parameterUpdates.has(move.binding));
  if (!moves.length) return;
  pauseAnimation();
  const started = performance.now();
  const step = (now: number) => {
    let running = false;
    for (const move of moves) {
      const k = Math.min(1, Math.max(0, now - started) / (move.seconds * 1000));
      const eased = k < 0.5 ? 2 * k * k : 1 - (2 - 2 * k) ** 2 / 2;
      parameterUpdates.get(move.binding)!(move.start + (move.end - move.start) * eased, true);
      running ||= k < 1;
    }
    if (running) beatFrame = requestAnimationFrame(step);
  };
  beatFrame = requestAnimationFrame(step);
}
function animateFrame(time: number) {
  if (!playing) return;
  if (time - animationTime < 1000 / 30) { animationFrame = requestAnimationFrame(animateFrame); return; }
  const delta = Math.min(100, time - animationTime); animationTime = time;
  const binding = element<HTMLSelectElement>('animation-parameter').value;
  const c = snapshot?.nodes.find(n => n.content.primitive === 'slider' && n.content.binding === binding)?.content;
  if (c?.primitive === 'slider') {
    const speed = Number(element<HTMLSelectElement>('animation-speed').value);
    const current = overrides.get(binding) ?? c.value;
    if (binding !== animationBinding) {
      animationBinding = binding;
      animationPhase = Math.acos(1 - 2 * (current - c.minimum) / (c.maximum - c.minimum));
    }
    animationPhase += delta / 12000 * speed * Math.PI * 2;
    const value = c.minimum + (1 - Math.cos(animationPhase)) / 2 * (c.maximum - c.minimum);
    parameterUpdates.get(binding)?.(value);
  }
  animationFrame = requestAnimationFrame(animateFrame);
}
let visualKey = '';
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
  const projectionLesson = plan?.recipe_id === 'vector_projection_v1';
  story.update(nodes, projectionLesson && !projectionModel);
  element('projection-views').hidden = !projectionLesson;
  element('viewport').hidden = projectionLesson && !projectionModel;
  if (!element('viewport').hidden) renderer?.render(nodes, params);
}
/** Smallest 1-2-5 step at least `raw`, so slider values read cleanly (0.05, not 0.03). */
function niceStep(raw: number) {
  const power = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map(m => m * power).find(step => step >= raw - 1e-12)!;
}
function renderSliders() {
  pauseAnimation(); parameterUpdates.clear();
  const choices = element<HTMLSelectElement>('animation-parameter'); choices.replaceChildren();
  const root = element('sliders'); root.replaceChildren(); renderer?.setXRSlider();
  element('playback').hidden = true;
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
    const update = (value: number, fromBeat = false) => {
      const bounded = Math.min(c.maximum, Math.max(c.minimum, value));
      overrides.set(c.binding, bounded); input.value = String(bounded); output.textContent = `${bounded.toFixed(projection ? 1 : 2)}${unit}`;
      if (!fromBeat) renderer?.setFocus([]);
      draw();
    };
    parameterUpdates.set(c.binding, update);
    choices.append(Object.assign(document.createElement('option'), { value: c.binding, textContent: name }));
    input.addEventListener('input', () => { pauseAnimation(); update(Number(input.value)); });
    label.append(output, input); root.append(label);
    if (!xrBound) { xrBound = true; renderer?.setXRSlider(direction => update(Number(input.value) + direction * (projection ? 5 : (c.maximum - c.minimum) / 20)), projection ? 'angle' : name); }
  }
  element('playback').hidden = parameterUpdates.size === 0;
  if (root.childElementCount) { const hint = document.createElement('p'); hint.className = 'hint'; hint.textContent = 'Each step of the explanation animates what it describes; select a step to replay it. Drag a slider or sweep freely to explore. Animation runs locally.'; root.append(hint); }
}
// Primary (teal) and secondary (orange) style meanings per authored recipe.
const legends: Record<string, string[]> = {
  vector_projection_v1: ['u / projection', 'v'],
  dijkstra_v1: ['nodes', 'shortest path'],
  learning_route_v1: ['known', 'your route'],
  mean_value_v1: ['f(x)', 'secant ∥ tangent'],
};
function showBeats(value?: Plan) {
  activeBeat = null;
  plan = value; const root = element('beats'); root.replaceChildren();
  if (value) element('legend').replaceChildren(...(legends[value.recipe_id] ?? []).map(text => Object.assign(document.createElement('span'), { textContent: `● ${text}` })));
  renderer?.setFocus([]); renderer?.setCaption('');
  if (!value) {
    pauseAnimation(); element('shadow-story').hidden = true; element('projection-views').hidden = true;
    element('viewport').hidden = false; element('math-panel').hidden = true; element('scene-caption').textContent = '';
    root.textContent = 'Send a new turn to receive the explanation.'; return;
  }
  renderer?.setMarker(value.generated ? 'AI-generated' : undefined);
  if (value.planned_by === 'claude' && !value.generated) { const note = document.createElement('p'); note.className = 'hint'; note.textContent = 'Example chosen by Claude · every number computed and checked by LUCID.'; root.append(note); }
  for (const [index, beat] of value.beats.entries()) {
    const button = document.createElement('button'); button.className = 'beat';
    const number = document.createElement('span'); number.textContent = String(index + 1).padStart(2, '0');
    const text = document.createElement('span'); typeset(text, beat.say_text);
    button.append(number, text); button.onclick = () => { selectBeat(index); if (voice) narrator.speakBeat(value, index); };
    root.append(button);
  }
  // Full scene initially visible; focus is an explicit reader action.
  renderer?.setCaption(value.beats[0]?.say_text ?? '');
  typeset(element('scene-caption'), value.beats[0]?.say_text ?? '');
  const formulas = snapshot?.nodes.filter(n => n.visible && n.content.primitive === 'text_label' && (n.id === 'formula' || n.content.text.includes('$') || n.content.text.includes('\\('))) ?? [];
  const mathPanel = element('math-panel'); mathPanel.replaceChildren(); mathPanel.hidden = formulas.length === 0;
  for (const node of formulas) if (node.content.primitive === 'text_label') {
    const equation = document.createElement('div'); typeset(equation, node.content.text); mathPanel.append(equation);
  }
  draw();
}
function selectBeat(index: number) {
  const beat = plan?.beats[index]; if (!beat) return;
  activeBeat = index;
  element('beats').querySelectorAll('button').forEach((button, i) => { if (i === index) button.setAttribute('aria-pressed', 'true'); else button.removeAttribute('aria-pressed'); });
  renderer?.setFocus(beat.focus_ids); renderer?.setCaption(beat.say_text);
  typeset(element('scene-caption'), beat.say_text);
  playBeat(beat);
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
    client.command('tutor', text, { action, view: learnerView() });
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
    if (!snapshot || value.scene_id !== snapshot.scene_id) { renderer?.beginScene(); visualKey = ''; }
    if (snapshot && (value.lesson_id !== snapshot.lesson_id || value.turn_epoch !== snapshot.turn_epoch)) showBeats();
    snapshot = value; overrides.clear(); element('empty').hidden = value.nodes.length > 0;
    element('scene-meta').textContent = `Epoch ${value.turn_epoch} · revision ${value.scene_revision} · ${value.nodes.length} objects`;
    draw(); renderSliders(); showCredits();
  },
  plan: value => {
    const key = value.concept_id ?? value.recipe_id;
    if (key !== visualKey) { renderer?.beginScene(); draw(); visualKey = key; }
    showBeats(value); renderSliders(); showTutor(value);
    void refreshReviews();
    // With voice, the narrator starts each beat's animation as it speaks. Otherwise play the first
    // beat that moves (tutor steps usually lead with spoken intro lines that do not).
    if (!voice) playBeat(value.beats.find(beat => beat.animations?.length));
    if (pendingVoiceTurn) { pendingVoiceTurn.resolve(value); pendingVoiceTurn = undefined; }
    else if (voice) narrator.introduce(value);
  },
  error: text => {
    error(text);
    pendingVoiceTurn?.reject(new Error(text)); pendingVoiceTurn = undefined;
  },
});
const models = new ModelCache(path => client.binary(path));
renderer?.setModels(models);
function showCredits() {
  const ids = (snapshot?.nodes ?? []).flatMap(node => node.visible && node.content.primitive === 'model3d' ? [node.content.model] : []);
  const credits = models.credits(ids);
  element('model-credits').textContent = credits.length ? `3D models · ${credits.join(' · ')}` : '';
  element('model-credits').hidden = !credits.length;
}
element('connection').addEventListener('submit', async event => {
  event.preventDefault(); clearError(); element<HTMLButtonElement>('connect').disabled = true;
  try {
    await client.connect(element<HTMLInputElement>('token').value); element<HTMLInputElement>('token').value = ''; element<HTMLButtonElement>('disconnect').disabled = false;
    element('topic-status').textContent = client.authorAvailable ? 'Topic writer configured. Ask for a new topic to generate a diagram lesson.' : 'Built-in lessons ready. New topics need ANTHROPIC_API_KEY in the backend terminal, then a backend restart.';
    element<HTMLButtonElement>('goal-start').disabled = false;
    element('learning-day').hidden = false; void refreshReviews();
    // Warm the 3D model cache so lessons that use a model show it without waiting.
    void client.api<{ models: ModelInfo[] }>('GET', '/api/models')
      .then(catalog => { models.setCatalog(catalog.models); showCredits(); return models.warm(); }).catch(() => undefined);
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
element('turn').addEventListener('submit', event => { event.preventDefault(); tutor('explore', element<HTMLTextAreaElement>('question').value); });
// ---- tutor: the learner talks or types; the route and scores stay on the server ----
let tutorState: TutorState | null = null;
function tutor(action: string, text = '') {
  clearError();
  try { client.command('tutor', text, { action, view: learnerView() }); } catch (e) { error((e as Error).message); }
}
/** What the learner is looking at that only the client knows, so explanations can build on it. */
function learnerView(): LearnerView {
  const sliders: Record<string, number> = {};
  for (const [binding, value] of overrides) {
    if (Number.isFinite(value) && Object.keys(sliders).length < 8) sliders[binding] = Math.round(value * 1000) / 1000;
  }
  const animating = playing && /^param\.[a-df-su-z]$/.test(animationBinding) ? animationBinding : null;
  return { active_beat: activeBeat !== null && activeBeat < 64 ? activeBeat : null, sliders, animating, in_vr: renderer?.presenting ?? false };
}
function showTutor(value: Plan) {
  if (!value.tutor) return;
  tutorState = value.tutor;
  element('reply').dataset.exercise = value.tutor.exercise ?? '';
  element('reply').dataset.step = value.tutor.total_steps ? `${value.tutor.step}/${value.tutor.total_steps}` : '';
  const awaiting = value.tutor.awaiting_answer;
  element('reply').hidden = false;
  element('reply-label').textContent = awaiting ? 'Your answer' : 'Ask the tutor anything';
  if (value.tutor.exercise && !['check', 'diagnostic', 'experience'].includes(value.tutor.exercise)) element('reply-label').textContent = `Your answer · ${value.tutor.exercise.startsWith('prediction') ? 'Predict and explain' : value.tutor.exercise === 'synthesis' ? 'Connect the ideas' : value.tutor.exercise === 'teachback' ? 'Teach it back' : 'Recall from memory'}`;
  element('reply-send').textContent = awaiting ? 'Answer' : 'Ask';
}
element('goal').addEventListener('submit', event => { event.preventDefault(); tutor(element<HTMLInputElement>('adaptive-route').checked ? 'goal' : 'explore', element<HTMLInputElement>('goal-text').value); });
element('reply').addEventListener('submit', event => {
  event.preventDefault();
  const input = element<HTMLInputElement>('reply-text'); const text = input.value;
  if (!text.trim() && !tutorState?.awaiting_answer) return;
  tutor(tutorState?.awaiting_answer ? 'answer' : 'question', text); input.value = '';
});
element('confused').onclick = () => { const input = element<HTMLInputElement>('reply-text'); tutor('confused', input.value || "I don't understand this part."); input.value = ''; };
element('continue').onclick = () => tutor('continue');
element('hint').onclick = () => tutor('hint');
for (const action of ['review', 'deeper', 'recall', 'skip']) {
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'quiet';
  button.textContent = ({ review: 'Review', deeper: 'Go deeper', recall: 'Active recall', skip: 'Skip' } as Record<string, string>)[action];
  button.onclick = () => { const input = element<HTMLInputElement>('reply-text'); tutor(action, input.value); input.value = ''; };
  element('continue').parentElement!.append(button);
}
const askButton = document.createElement('button');
askButton.type = 'button'; askButton.className = 'quiet'; askButton.textContent = 'Ask a question';
askButton.onclick = () => { const input = element<HTMLInputElement>('reply-text'); if (input.value.trim()) tutor('question', input.value); input.value = ''; };
element('continue').parentElement!.append(askButton);

type ReviewTopic = { concept_id: string; name: string; mastery: number; due: boolean; next_review: string; recall_probability: number | null };
let reviewRequest = 0;
async function refreshReviews() {
  const request = ++reviewRequest;
  try {
    const due = await client.api<{ due_count: number; topics: ReviewTopic[] }>('GET', '/api/me/reviews');
    if (request !== reviewRequest || !sessionOpen) return;
    element('review-status').textContent = due.due_count ? `${due.due_count} topics ready for retrieval. Warm-ups cover up to five topics.` : 'No reviews due. You can learn something new or practice recall anyway.';
    element('review-topics').replaceChildren(...due.topics.slice(0, 5).map(topic => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'quiet review-topic';
      const recall = topic.recall_probability === null ? 'Recall not estimated yet' : `Estimated recall ${Math.round(topic.recall_probability * 100)}%`;
      button.textContent = `${topic.name} · mastery ${topic.mastery}/10 · ${recall} · ${topic.due ? 'Due now' : `Review ${new Date(topic.next_review).toLocaleDateString()}`}`;
      button.onclick = () => tutor('recall', topic.concept_id); return button;
    }));
  } catch { if (request === reviewRequest) element('review-status').textContent = 'Review suggestions are unavailable. You can still choose a topic.'; }
}
element('warmup').onclick = () => tutor('warmup');
element('learn-new').onclick = () => element<HTMLInputElement>('goal-text').focus();
const manipulation = element<HTMLSelectElement>('manipulation');
const wholeModel = element<HTMLInputElement>('whole-model');
const updateManipulation = () => renderer?.setManipulation(manipulation.value as 'orbit' | 'translate' | 'rotate', wholeModel.checked);
manipulation.onchange = updateManipulation; wholeModel.onchange = updateManipulation;
element('reset-arrangement').onclick = () => renderer?.resetArrangement();
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
    if (plan) narrator.introduce(plan);
  } catch (e) { voiceStatus(e instanceof Error ? e.message : 'Voice failed to start.'); }
  finally { updateVoiceControls(); }
};
element('mute').onclick = () => { muted = !muted; applyMic(); voiceStatus(muted ? 'Microphone muted.' : 'Listening…'); updateVoiceControls(); };
element<HTMLInputElement>('barge-in').onchange = event => { narrator.bargeIn = (event.target as HTMLInputElement).checked; };
// VR: a trigger pull that misses the slider buttons cuts the tutor off and opens the mic.
renderer?.setXRInterrupt(() => narrator.stop());
element('interrupt').onclick = () => { clearError(); narrator.stop(); try { client.command('interrupt'); showBeats(); } catch (e) { error((e as Error).message); } };
element('reset').onclick = () => renderer?.resetView();
function projectionView(model: boolean) {
  projectionModel = model;
  element('view-story').setAttribute('aria-pressed', String(!model));
  element('view-model').setAttribute('aria-pressed', String(model)); draw();
}
element('view-story').onclick = () => projectionView(false);
element('view-model').onclick = () => projectionView(true);
element('play-animation').onclick = () => {
  if (playing) { pauseAnimation(); return; }
  animationBinding = '';
  playing = true; animationTime = performance.now(); element('play-animation').textContent = 'Pause sweep';
  animationFrame = requestAnimationFrame(animateFrame);
};
element('restart-animation').onclick = () => {
  pauseAnimation();
  for (const node of snapshot?.nodes ?? []) if (node.content.primitive === 'slider') parameterUpdates.get(node.content.binding)?.(node.content.value);
  overrides.clear(); draw();
};
document.addEventListener('visibilitychange', () => { if (document.hidden) pauseAnimation(); });
window.addEventListener('pagehide', () => { stopVoice(); void client.disconnect(); renderer?.dispose(); });
