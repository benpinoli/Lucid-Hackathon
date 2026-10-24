import type { Plan } from './protocol';

export type RealtimeEvent = { type: string; [key: string]: unknown };
const ACTIONS = ['goal', 'answer', 'confused', 'question', 'continue', 'explore'];
type Hooks = {
  send: (event: object) => void;
  /** Sends the learner's words to the tutor; resolves with the reply (diagram + steps). */
  tutor: (action: string, text: string) => Promise<Plan>;
  /** Highlight cue, scheduled on the playback clock of the spoken script. */
  focusBeat: (index: number) => void;
  status: (text: string) => void;
  /** Turn-taking: closed while the tutor speaks; the learner's own mute still wins. */
  setMicOpen: (open: boolean) => void;
};
type Response = { id?: string; status?: string; metadata?: Record<string, string> | null; output?: { type?: string; name?: string; call_id?: string; arguments?: string }[] };
type Script = { tag: string; beats: string[]; first: number; awaiting: boolean; id?: string; startedAt?: number };

const SCRIPT_PROMPT = 'Read the following script aloud exactly as written, word for word, at a calm teaching pace, '
  + 'with a short natural pause between paragraphs. Do not add, skip or change anything, and say nothing after it.\n\n';
const PARAGRAPH_PAUSE = 0.35; // seconds of pause assumed between beats
const TAIL_MS = 350; // keep the mic closed briefly after playback so trailing echo is not heard
const SLACK_MS = 4000; // if the drain event never arrives, reopen this long after the estimate

/**
 * Speaks each tutor reply as one continuous response and owns turn-taking. Pure event state
 * machine: it never touches WebRTC, so it is testable with recorded provider events.
 */
export class VoiceNarrator {
  /** Let speech interrupt the tutor. Off by default: echo and noise would cut it off. */
  bargeIn = false;
  /** Speaking rate used to place highlights; recalibrated from each completed script. */
  charsPerSecond = 15;
  private script?: Script;
  private generation = 0;
  private requested = false;
  private responseActive = false;
  private playing = new Set<string>();
  private timers: ReturnType<typeof setTimeout>[] = [];
  private fallback?: ReturnType<typeof setTimeout>;
  private reopen?: ReturnType<typeof setTimeout>;
  private micOpen = true;
  constructor(private hooks: Hooks) {}

  /** Speak a whole reply: explanation and any question, without gaps. */
  narrate(plan: Plan) { this.speak(plan.beats.map(beat => beat.say_text), 0, !!plan.tutor?.awaiting_answer); }
  /** Speak one beat only, e.g. when the learner selects it. */
  speakBeat(plan: Plan, index: number) { this.speak([plan.beats[index].say_text], index, false); }
  /** Tell the model about a lesson it did not request, so follow-up questions have context. */
  introduce(plan: Plan) {
    const steps = plan.beats.map((beat, i) => `${i + 1}. ${beat.say_text}`).join(' ');
    this.hooks.send({ type: 'conversation.item.create', item: { type: 'message', role: 'system', content: [{ type: 'input_text', text: `The learner is now viewing the ${plan.recipe_id} lesson. Its steps are: ${steps}` }] } });
    this.narrate(plan);
  }
  /** Abandon the current script and cut any audio still playing. */
  stop() {
    this.drop();
    if (this.responseActive) this.hooks.send({ type: 'response.cancel' });
    if (this.playing.size) this.hooks.send({ type: 'output_audio_buffer.clear' });
    this.updateMic();
  }

  handle(event: RealtimeEvent) {
    const response = event.response as Response | undefined;
    const id = typeof event.response_id === 'string' ? event.response_id : undefined;
    switch (event.type) {
      case 'input_audio_buffer.speech_started':
        // With the mic closed this can only be echo of the tutor's own voice: ignore it.
        if (this.script && !this.bargeIn) break;
        if (this.script) this.drop(); // Barge-in: the provider truncates its own audio.
        this.hooks.status('Listening…');
        break;
      case 'response.created':
        this.responseActive = true;
        if (this.requested && this.script && response?.metadata?.lucid_script === this.script.tag) {
          this.script.id = response.id; this.requested = false;
        }
        break;
      case 'output_audio_buffer.started':
        if (id) this.playing.add(id);
        this.updateMic();
        if (id && id === this.script?.id) this.started(this.script);
        break;
      case 'output_audio_buffer.stopped': case 'output_audio_buffer.cleared':
        if (id) this.playing.delete(id);
        if (id && id === this.script?.id) this.finish(event.type === 'output_audio_buffer.stopped');
        else this.pump();
        this.updateMic();
        break;
      case 'response.done':
        this.responseActive = false;
        if (response?.id && response.id === this.script?.id) {
          if (response.status !== 'completed') this.finish(false);
          else {
            // The drain event can lag or go missing; never leave the mic closed.
            const script = this.script;
            const remaining = this.duration(script.beats) - (script.startedAt ? (performance.now() - script.startedAt) / 1000 : 0);
            clearTimeout(this.fallback);
            this.fallback = setTimeout(() => {
              if (this.script !== script) return;
              if (script.id) this.playing.delete(script.id);
              this.finish(false);
              this.updateMic();
            }, Math.max(0, remaining) * 1000 + SLACK_MS);
          }
        }
        for (const item of response?.output ?? []) if (item.type === 'function_call' && item.name === 'tutor' && item.call_id) void this.runTool(item.call_id, item.arguments);
        this.pump();
        this.updateMic();
        break;
      case 'error':
        this.hooks.status('Voice provider reported an error; text controls still work.');
        break;
    }
  }

  private speak(beats: string[], first: number, awaiting: boolean) {
    this.stop();
    this.script = { tag: String(this.generation), beats, first, awaiting };
    this.pump();
    this.updateMic();
  }
  /** Request the script once no other response is generating or playing. */
  private pump() {
    const script = this.script;
    if (!script || script.id || this.requested || this.responseActive || this.playing.size) return;
    this.requested = true;
    this.hooks.send({ type: 'response.create', response: {
      instructions: SCRIPT_PROMPT + script.beats.join('\n\n'), metadata: { lucid_script: script.tag } } });
  }
  private duration(beats: string[]) {
    return beats.reduce((sum, text) => sum + text.length, 0) / this.charsPerSecond + PARAGRAPH_PAUSE * (beats.length - 1);
  }
  private started(script: Script) {
    script.startedAt = performance.now();
    this.hooks.status('Speaking…');
    let offset = 0;
    script.beats.forEach((text, i) => {
      const focus = () => { if (this.script === script) this.hooks.focusBeat(script.first + i); };
      if (i === 0) focus(); // with the first audio, not a tick later
      else this.timers.push(setTimeout(focus, offset * 1000));
      offset += text.length / this.charsPerSecond + PARAGRAPH_PAUSE;
    });
  }
  private finish(drained: boolean) {
    const script = this.script;
    if (script?.startedAt && drained) {
      // Calibrate only from plausible measurements: a late drain event would skew the rate.
      const seconds = (performance.now() - script.startedAt) / 1000 - PARAGRAPH_PAUSE * (script.beats.length - 1);
      const measured = script.beats.reduce((sum, text) => sum + text.length, 0) / Math.max(seconds, 0.1);
      if (measured > this.charsPerSecond * 0.6 && measured < this.charsPerSecond * 1.6) {
        this.charsPerSecond = Math.min(22, Math.max(9, this.charsPerSecond * 0.7 + measured * 0.3));
      }
    }
    const awaiting = !!script?.awaiting;
    this.drop();
    this.hooks.status(awaiting ? 'Your turn. Take your time.' : 'Listening…');
  }
  private drop() {
    this.generation++; this.script = undefined; this.requested = false;
    this.timers.forEach(clearTimeout); this.timers = []; clearTimeout(this.fallback);
  }
  private updateMic() {
    const open = this.bargeIn || (!this.script && !this.requested && this.playing.size === 0);
    if (open === this.micOpen) { if (!open) clearTimeout(this.reopen); return; }
    clearTimeout(this.reopen);
    if (!open) { this.micOpen = false; this.hooks.setMicOpen(false); return; }
    this.reopen = setTimeout(() => { this.micOpen = true; this.hooks.setMicOpen(true); }, TAIL_MS);
  }
  private async runTool(callId: string, raw?: string) {
    let output: object; let plan: Plan | undefined;
    try {
      const { action, text } = JSON.parse(raw ?? '{}');
      if (!ACTIONS.includes(action) || typeof text !== 'string' || text.length > 2000) throw new Error('Use a known action and the learner\'s words.');
      this.hooks.status(action === 'confused' ? 'Finding another way to explain…' : 'Thinking…');
      plan = await this.hooks.tutor(action, text.trim());
      output = { steps: plan.beats.map(beat => beat.say_text), awaiting_answer: !!plan.tutor?.awaiting_answer,
        note: 'The app now reads these steps aloud. Do not repeat them.' + (plan.tutor?.awaiting_answer
          ? ' A question is pending: when the learner replies, call tutor with action answer and their words.' : '') };
    } catch (error) { output = { error: error instanceof Error ? error.message : 'Lesson failed' }; }
    this.hooks.send({ type: 'conversation.item.create', item: { type: 'function_call_output', call_id: callId, output: JSON.stringify(output) } });
    if (plan) this.narrate(plan);
    // Let the model briefly explain a failure instead of going silent.
    else this.hooks.send({ type: 'response.create' });
  }
}

export type VoiceConnection = { send: (event: object) => void; setMuted: (muted: boolean) => void; close: () => void };
export type Credential = { client_secret: string; sdp_url: string };

/** Browser-only WebRTC session: microphone up, speech down, events on a data channel. */
export async function connectVoice(credential: Credential, onEvent: (event: RealtimeEvent) => void, onClosed: () => void): Promise<VoiceConnection> {
  let microphone: MediaStream;
  // Ask explicitly: some browsers and headsets default these off, and echo breaks turn-taking.
  try { microphone = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }); }
  catch { throw new Error('Microphone permission was denied or no microphone is available.'); }
  const pc = new RTCPeerConnection();
  const audio = new Audio(); audio.autoplay = true;
  pc.ontrack = event => { audio.srcObject = event.streams[0]; };
  pc.addTrack(microphone.getAudioTracks()[0], microphone);
  const channel = pc.createDataChannel('oai-events');
  let closed = false;
  const close = () => {
    if (closed) return; closed = true;
    channel.close(); pc.close(); microphone.getTracks().forEach(track => track.stop()); audio.srcObject = null; onClosed();
  };
  channel.onmessage = ({ data }) => {
    try {
      if (typeof data !== 'string' || data.length > 262144) return;
      const event = JSON.parse(data);
      if (event && typeof event.type === 'string') onEvent(event);
    } catch { /* ignore malformed provider events; the session continues */ }
  };
  channel.onclose = close;
  pc.onconnectionstatechange = () => { if (pc.connectionState === 'failed' || pc.connectionState === 'closed') close(); };
  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const answer = await fetch(credential.sdp_url, { method: 'POST', body: offer.sdp, headers: { Authorization: `Bearer ${credential.client_secret}`, 'Content-Type': 'application/sdp' }, signal: AbortSignal.timeout(10000) });
    if (!answer.ok) throw new Error(`Voice provider returned ${answer.status}.`);
    await pc.setRemoteDescription({ type: 'answer', sdp: await answer.text() });
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Voice connection timed out.')), 10000);
      channel.onopen = () => { clearTimeout(timer); resolve(); };
    });
  } catch (error) { close(); throw error; }
  return {
    send: event => { if (channel.readyState === 'open') channel.send(JSON.stringify(event)); },
    setMuted: muted => microphone.getAudioTracks().forEach(track => { track.enabled = !muted; }),
    close,
  };
}
