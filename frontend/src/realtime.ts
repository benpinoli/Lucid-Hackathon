import type { Plan } from './protocol';
import type { RealtimeEvent } from './voice';

type Call = { type?: string; name?: string; call_id?: string; arguments?: string };
type Response = { id?: string; status?: string; output?: Call[] };
type Hooks = {
  send: (event: object) => void;
  tool: (name: string, args: object, callId: string) => Promise<Record<string, unknown>>;
  focusBeat: (index: number) => void;
  status: (text: string) => void;
  setMicOpen: (open: boolean) => void;
  transcript: (text: string) => void;
  interrupt?: () => Promise<void>;
};

/** One model owns conversation, planning and coding. Tools only validate and store its work. */
export class RealtimeTeacher {
  bargeIn = false;
  audio = true;
  private active = false;
  private generation = 0;
  private running = false;
  private responseRequested = false;
  private playing = new Set<string>();
  private handled = new Set<string>();
  private tools = Promise.resolve();
  private speak?: string;
  private shouldResume = false;
  private focusPending = false;
  private transcriptText = '';
  private reopen?: ReturnType<typeof setTimeout>;
  private cancellation = Promise.resolve();
  private responseGenerations = new Map<string, number>();
  constructor(private hooks: Hooks) {}

  configure(audio: boolean) {
    this.audio = audio;
    this.hooks.send({ type: 'session.update', session: { type: 'realtime', output_modalities: audio ? ['audio'] : ['text'] } });
    this.updateMic();
  }

  async request(action: string, text: string, adaptive: boolean) {
    this.stop();
    await this.cancellation;
    const generation = this.generation;
    this.running = true; this.updateMic();
    try {
      // Refresh the exact screen and brain before every typed request, just as for spoken tools.
      const context = await this.hooks.tool('get_learning_context', { topic: '', learner_text: text || action }, crypto.randomUUID());
      if (generation !== this.generation) return;
      this.hooks.send({ type: 'conversation.item.create', item: { type: 'message', role: 'system', content: [
        { type: 'input_text', text: `Current learner context: ${JSON.stringify(context)}. Adaptive route enabled: ${adaptive}.` },
      ] } });
      this.hooks.send({ type: 'conversation.item.create', item: { type: 'message', role: 'user', content: [
        { type: 'input_text', text: JSON.stringify({ action, text: text || action }) },
      ] } });
      this.shouldResume = true;
    } finally {
      if (generation === this.generation) { this.running = false; this.resume(); this.updateMic(); }
    }
  }

  speakBeat(plan: Plan, index: number) {
    const beat = plan.beats[index]; if (!beat) return;
    this.stop(); this.speak = beat.say_text; this.shouldResume = true; this.resume();
  }

  stop() {
    const wasActive = this.active || this.responseRequested || this.running || this.playing.size > 0;
    this.generation++; this.running = false; this.shouldResume = false; this.focusPending = false;
    this.speak = undefined; clearTimeout(this.reopen);
    if (this.active || this.responseRequested) this.hooks.send({ type: 'response.cancel' });
    if (this.playing.size) this.hooks.send({ type: 'output_audio_buffer.clear' });
    this.active = false; this.responseRequested = false; this.playing.clear(); this.updateMic();
    if (wasActive && this.hooks.interrupt) {
      this.cancellation = this.hooks.interrupt().catch(() => { this.hooks.status('Could not refresh the interrupted scene. Reconnect before continuing.'); });
    }
  }

  handle(event: RealtimeEvent) {
    const response = event.response as Response | undefined;
    const id = typeof event.response_id === 'string' ? event.response_id : '';
    switch (event.type) {
      case 'response.created':
        if (response?.id) {
          this.responseGenerations.set(response.id, this.generation);
          if (this.responseGenerations.size > 512) this.responseGenerations.delete(this.responseGenerations.keys().next().value!);
        }
        this.active = true; this.responseRequested = false;
        this.transcriptText = ''; break;
      case 'response.output_text.delta': case 'response.output_audio_transcript.delta':
        if (typeof event.delta === 'string') {
          this.transcriptText += event.delta; this.hooks.transcript(this.transcriptText);
        }
        break;
      case 'output_audio_buffer.started':
        if (id) this.playing.add(id);
        if (this.focusPending) { this.focusPending = false; this.hooks.focusBeat(0); }
        this.hooks.status('Speaking…'); break;
      case 'output_audio_buffer.stopped': case 'output_audio_buffer.cleared':
        if (id) this.playing.delete(id);
        this.resume(); break;
      case 'input_audio_buffer.speech_started':
        if (this.bargeIn) this.stop();
        this.hooks.status('Listening…'); break;
      case 'response.done': {
        const responseGeneration = response?.id ? this.responseGenerations.get(response.id) : undefined;
        if (responseGeneration !== undefined && responseGeneration !== this.generation) break;
        this.active = false; this.responseRequested = false;
        const calls = response?.status === 'completed' ? (response.output ?? []).filter(c => c.type === 'function_call' && c.call_id && !this.handled.has(c.call_id)) : [];
        if (calls.length) {
          const generation = this.generation;
          this.tools = this.tools.then(() => this.runCalls(calls, generation));
        }
        if (!this.playing.size && !calls.length) this.hooks.status(this.audio ? 'Listening…' : 'Ready for your next message.');
        // Some clients omit the drained event. Never leave the microphone stuck closed.
        clearTimeout(this.reopen);
        this.reopen = setTimeout(() => { if (!this.active && !this.running) { this.playing.clear(); this.resume(); this.updateMic(); } }, 15000);
        break;
      }
      case 'error':
        this.active = false; this.responseRequested = false;
        this.hooks.status('Realtime reported an error. Try your request again.'); break;
    }
    this.updateMic();
  }

  private async runCalls(calls: Call[], generation: number) {
    await this.cancellation;
    if (generation !== this.generation) return;
    this.running = true; this.updateMic();
    try {
      for (const call of calls) {
        if (!call.call_id || !call.name || this.handled.has(call.call_id)) continue;
        this.handled.add(call.call_id);
        if (this.handled.size > 512) this.handled.delete(this.handled.values().next().value!);
        let output: Record<string, unknown>;
        try {
          if ((call.arguments?.length ?? 0) > 262144) throw new Error('Tool arguments exceed the scene budget.');
          const args = JSON.parse(call.arguments || '{}');
          if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('Tool arguments must be an object.');
          output = await this.hooks.tool(call.name, args, call.call_id);
        } catch (error) {
          output = { error: error instanceof Error ? error.message : 'Tool failed. Fix the arguments and try again.' };
        }
        if (generation !== this.generation) return;
        this.hooks.send({ type: 'conversation.item.create', item: {
          type: 'function_call_output', call_id: call.call_id, output: JSON.stringify(output),
        } });
        if (typeof output.speak_text === 'string') {
          this.speak = output.speak_text; this.focusPending = true;
        }
      }
      this.shouldResume = true;
    } finally {
      if (generation === this.generation) { this.running = false; this.resume(); this.updateMic(); }
    }
  }

  private resume() {
    if (!this.shouldResume || this.active || this.responseRequested || this.running || this.playing.size) return;
    this.shouldResume = false; this.responseRequested = true;
    const response: Record<string, unknown> = { output_modalities: this.audio ? ['audio'] : ['text'] };
    if (this.speak !== undefined) {
      response.instructions = `The scene is ready. Say this one teaching step, then stop and listen. Do not add another step or call tools in this response:\n${this.speak}`;
      response.tool_choice = 'none'; this.speak = undefined;
      if (!this.audio && this.focusPending) { this.focusPending = false; this.hooks.focusBeat(0); }
    }
    this.hooks.send({ type: 'response.create', response }); this.updateMic();
  }

  private updateMic() {
    this.hooks.setMicOpen(this.audio && (this.bargeIn || !(this.active || this.responseRequested || this.running || this.playing.size)));
  }
}
