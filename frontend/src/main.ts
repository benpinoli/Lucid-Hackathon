import './style.css';
import { LessonClient } from './client';
import { LessonRenderer } from './renderer';
import { localProjection, type Plan, type Snapshot } from './protocol';

document.querySelector<HTMLDivElement>('#app')!.innerHTML = `
  <header><a class="brand" href="/">LUCID<span>LEARNING IN SPACE</span></a><span class="mode">DESKTOP / WEBXR</span><span id="status" role="status">Disconnected</span></header>
  <main>
    <aside>
      <div class="eyebrow">YOUR LEARNING STUDIO</div><h1>Make room for<br>understanding.</h1>
      <p class="intro">Explore ideas in a space you can move through. Start with vectors, then follow their projection.</p>
      <form id="connection"><label for="token">Private backend token</label><input id="token" type="password" autocomplete="off" placeholder="Paste the demo token" required /><div class="row"><button id="connect">Connect</button><button id="disconnect" type="button" class="quiet" disabled>Disconnect</button></div><p class="hint">Held in memory for this session only.</p></form>
      <form id="turn"><label for="question">What would you like to explore?</label><textarea id="question" maxlength="2000" rows="3">Explain dot product</textarea><div class="row"><button id="send" disabled>Explore concept <span>↗</span></button><button id="interrupt" type="button" class="quiet" disabled>Interrupt</button></div></form>
      <div id="error" role="alert" hidden></div>
      <section class="lesson"><div class="eyebrow">GUIDED EXPLANATION</div><div id="beats"><p class="hint">Connect and explore a concept to begin.</p></div></section>
      <p class="footnote">Authored lessons · Text mode<br>No inference provider required</p>
    </aside>
    <section class="workspace" aria-label="Lesson workspace"><div class="toolbar"><div><span class="live-dot"></span><strong>Concept space</strong><span id="scene-meta">Awaiting lesson</span></div><div class="row"><button id="reset" class="quiet">Reset view</button><div id="xr"></div></div></div>
      <div id="viewport"><div id="empty"><span>01 / VECTOR LAB</span><h2>A new perspective<br>starts here.</h2><p>Connect your backend to bring the lesson into view.</p></div></div>
      <div class="scene-bottom"><div class="legend"><span>● u / projection</span><span>● v</span></div><span>Drag to orbit · Scroll to zoom</span></div>
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
  const angleSlider = nodes.find(n => n.visible && n.content.primitive === 'slider' && n.content.binding === 'projection.angle');
  if (plan?.recipe_id === 'vector_projection_v1' && angleSlider?.content.primitive === 'slider') {
    const angle = overrides.get('projection.angle');
    const length = overrides.get('projection.length');
    if (angle !== undefined || length !== undefined) nodes = localProjection(nodes, angle ?? angleSlider.content.value, length);
  }
  renderer?.render(nodes);
}
function renderSliders() {
  const root = element('sliders'); root.replaceChildren(); renderer?.setXRSlider();
  if (!snapshot || plan?.recipe_id !== 'vector_projection_v1') return;
  for (const node of snapshot.nodes) {
    const c = node.content; if (!node.visible || c.primitive !== 'slider') continue;
    const label = document.createElement('label'); label.textContent = c.binding === 'projection.angle' ? 'Vector angle' : 'Vector length';
    const output = document.createElement('output'); output.textContent = `${c.value.toFixed(1)}${c.binding === 'projection.angle' ? '°' : ''}`;
    const input = document.createElement('input'); input.type = 'range'; input.min = String(c.minimum); input.max = String(c.maximum); input.step = '0.1'; input.value = String(c.value); input.setAttribute('aria-label', label.textContent);
    const update = (value: number) => {
      const bounded = Math.min(c.maximum, Math.max(c.minimum, value));
      overrides.set(c.binding, bounded); input.value = String(bounded); output.textContent = `${bounded.toFixed(1)}${c.binding === 'projection.angle' ? '°' : ''}`;
      renderer?.setFocus([]); renderer?.setCaption('Local exploration · reset the lesson with a new turn.');
      draw();
    };
    input.addEventListener('input', () => update(Number(input.value)));
    label.append(output, input); root.append(label);
    if (c.binding === 'projection.angle') renderer?.setXRSlider(direction => update(Number(input.value) + direction * 5));
  }
  if (root.childElementCount) { const hint = document.createElement('p'); hint.className = 'hint'; hint.textContent = 'Explore locally · sliders update this view without network calls. Teaching text describes the original scene.'; root.append(hint); }
}
function showBeats(value?: Plan) {
  plan = value; const root = element('beats'); root.replaceChildren();
  renderer?.setFocus([]); renderer?.setCaption('');
  if (!value) { root.textContent = 'Send a new turn to receive the explanation.'; return; }
  for (const [index, beat] of value.beats.entries()) {
    const button = document.createElement('button'); button.className = 'beat';
    const number = document.createElement('span'); number.textContent = String(index + 1).padStart(2, '0');
    const text = document.createElement('span'); text.textContent = beat.say_text;
    button.append(number, text); button.onclick = () => {
      root.querySelectorAll('button').forEach(b => b.removeAttribute('aria-pressed'));
      button.setAttribute('aria-pressed', 'true'); renderer?.setFocus(beat.focus_ids); renderer?.setCaption(beat.say_text);
    }; root.append(button);
  }
  // Full scene initially visible; focus is an explicit reader action.
  renderer?.setCaption(value.beats[0]?.say_text ?? '');
}
const client = new LessonClient({
  status: (text, ready) => {
    element('status').textContent = text; element('status').classList.toggle('connected', ready);
    element<HTMLButtonElement>('send').disabled = !ready; element<HTMLButtonElement>('interrupt').disabled = !ready;
  },
  state: value => {
    if (snapshot && (value.lesson_id !== snapshot.lesson_id || value.turn_epoch !== snapshot.turn_epoch)) showBeats();
    snapshot = value; overrides.clear(); element('empty').hidden = value.nodes.length > 0;
    element('scene-meta').textContent = `Epoch ${value.turn_epoch} · revision ${value.scene_revision} · ${value.nodes.length} objects`;
    draw(); renderSliders();
  },
  plan: value => { showBeats(value); renderSliders(); }, error,
});
element('connection').addEventListener('submit', async event => {
  event.preventDefault(); clearError(); element<HTMLButtonElement>('connect').disabled = true;
  try { await client.connect(element<HTMLInputElement>('token').value); element<HTMLInputElement>('token').value = ''; element<HTMLButtonElement>('disconnect').disabled = false; }
  catch (e) { error(e instanceof Error ? e.message : 'Connection failed'); }
  finally { element<HTMLButtonElement>('connect').disabled = false; }
});
element('disconnect').onclick = async () => {
  await client.disconnect(); snapshot = undefined; showBeats(); overrides.clear(); renderer?.render([]); renderer?.setXRSlider();
  element('sliders').replaceChildren(); element('empty').hidden = false; element('scene-meta').textContent = 'Awaiting lesson'; element<HTMLButtonElement>('disconnect').disabled = true;
};
element('turn').addEventListener('submit', event => { event.preventDefault(); clearError(); try { client.command('turn', element<HTMLTextAreaElement>('question').value); } catch (e) { error((e as Error).message); } });
element('interrupt').onclick = () => { clearError(); try { client.command('interrupt'); showBeats(); } catch (e) { error((e as Error).message); } };
element('reset').onclick = () => renderer?.resetView();
window.addEventListener('pagehide', () => { void client.disconnect(); renderer?.dispose(); });
