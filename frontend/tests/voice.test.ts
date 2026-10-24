import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { VoiceNarrator } from '../src/voice';
import type { Plan } from '../src/protocol';

// 'Start at A.' is 11 characters: at 15 chars/s plus a 0.35 s paragraph pause, beat 2 lands at ~1083 ms.
const plan: Plan = { recipe_id: 'dijkstra_v1', beats: [
  { beat_id: 'start', say_text: 'Start at A.', focus_ids: ['node_A'] },
  { beat_id: 'finish', say_text: 'The path costs 11.', focus_ids: ['edge_D_E'] },
] };
const asking: Plan = { ...plan, tutor: { mode: 'checking', awaiting_answer: true, goal: 'Dijkstra' } };

function setup(tutor = vi.fn(async (_action: string, _text: string) => plan)) {
  const sent: any[] = []; const focused: number[] = []; const mic: boolean[] = []; const statuses: string[] = [];
  const narrator = new VoiceNarrator({ send: event => sent.push(event), tutor, focusBeat: i => focused.push(i),
    status: text => statuses.push(text), setMicOpen: open => mic.push(open) });
  const creates = () => sent.filter(event => event.type === 'response.create');
  /** The provider accepts the latest script request and starts playing it. */
  const play = (id: string) => {
    narrator.handle({ type: 'response.created', response: { id, metadata: creates().at(-1).response.metadata } });
    narrator.handle({ type: 'output_audio_buffer.started', response_id: id });
  };
  const finish = (id: string) => {
    narrator.handle({ type: 'response.done', response: { id, status: 'completed', output: [] } });
    narrator.handle({ type: 'output_audio_buffer.stopped', response_id: id });
  };
  return { narrator, sent, focused, mic, statuses, creates, play, finish, tutor };
}

beforeEach(() => { vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance', 'Date'] }); });
afterEach(() => { vi.useRealTimers(); });

describe('voice narrator', () => {
  it.each(['review', 'deeper'])('forwards %s and graph context while a check is pending', async action => {
    const reply: Plan = { ...asking, graph_context: { teaching_sequence: [{ name: 'Functions' }, { name: 'Derivatives' }] } };
    const tutor = vi.fn(async () => reply);
    const { narrator, sent, creates } = setup(tutor);
    narrator.handle({ type: 'response.done', response: { id: 'tool', status: 'completed', output: [
      { type: 'function_call', name: 'tutor', call_id: 'more', arguments: JSON.stringify({ action, text: 'teach me more' }) },
    ] } });
    await vi.waitFor(() => expect(creates()).toHaveLength(1));
    expect(tutor).toHaveBeenCalledWith(action, 'teach me more');
    const output = JSON.parse(sent.find(event => event.item?.type === 'function_call_output').item.output);
    expect(output.learning_context).toEqual(reply.graph_context);
    expect(output.awaiting_answer).toBe(true);
  });

  it('speaks a whole reply as one response, highlighting on the playback clock', async () => {
    const { narrator, sent, focused, mic, statuses, creates, play, finish, tutor } = setup();
    narrator.handle({ type: 'response.created', response: { id: 'tool' } });
    narrator.handle({ type: 'response.done', response: { id: 'tool', status: 'completed', output: [{ type: 'function_call', name: 'tutor', call_id: 'call_1', arguments: '{"action":"confused","text":"why is B 3?"}' }] } });
    await vi.waitFor(() => expect(creates()).toHaveLength(1));
    expect(tutor).toHaveBeenCalledWith('confused', 'why is B 3?');
    const output = sent.find(event => event.item?.type === 'function_call_output');
    expect(JSON.parse(output.item.output)).toMatchObject({ steps: ['Start at A.', 'The path costs 11.'], awaiting_answer: false });
    // One request carries every beat: no gaps between sentences.
    expect(creates()[0].response.instructions).toContain('Start at A.\n\nThe path costs 11.');
    expect(mic).toEqual([false]);  // closed as soon as the tutor is about to speak
    play('r1');
    expect(focused).toEqual([0]);  // first highlight with the first audio
    vi.advanceTimersByTime(1000);
    expect(focused).toEqual([0]);
    vi.advanceTimersByTime(150);
    expect(focused).toEqual([0, 1]);
    finish('r1');
    expect(statuses.at(-1)).toBe('Listening…');
    expect(mic).toEqual([false]);  // short tail so trailing echo is not heard
    vi.advanceTimersByTime(400);
    expect(mic).toEqual([false, true]);
    expect(creates()).toHaveLength(1);
  });

  it('gives the learner the floor after a question', () => {
    const { narrator, statuses, play, finish, mic } = setup();
    narrator.narrate(asking);
    play('r1'); vi.advanceTimersByTime(3000); finish('r1'); vi.advanceTimersByTime(400);
    expect(statuses.at(-1)).toBe('Your turn. Take your time.');
    expect(mic.at(-1)).toBe(true);
  });

  it('ignores echo of its own voice while speaking', () => {
    const { narrator, focused, creates, play, mic } = setup();
    narrator.narrate(asking);
    play('r1');
    narrator.handle({ type: 'input_audio_buffer.speech_started' });
    vi.advanceTimersByTime(1200);
    expect(focused).toEqual([0, 1]);  // the question is still being asked
    expect(creates()).toHaveLength(1);
    expect(mic).toEqual([false]);
  });

  it('lets speech interrupt when barge-in is on', () => {
    const { narrator, focused, play, mic, statuses } = setup();
    narrator.bargeIn = true;
    narrator.narrate(plan);
    play('r1');
    narrator.handle({ type: 'input_audio_buffer.speech_started' });
    vi.advanceTimersByTime(2000);
    expect(focused).toEqual([0]);
    expect(mic).toEqual([]);  // never closed
    expect(statuses.at(-1)).toBe('Listening…');
  });

  it('reopens the mic even if the drain event never arrives', () => {
    const { narrator, play, mic } = setup();
    narrator.narrate(plan);
    play('r1');
    narrator.handle({ type: 'response.done', response: { id: 'r1', status: 'completed', output: [] } });
    // Estimated 2.3 s of speech + 4 s slack + 0.35 s tail.
    vi.advanceTimersByTime(6000);
    expect(mic).toEqual([false]);
    vi.advanceTimersByTime(1000);
    expect(mic).toEqual([false, true]);
  });

  it("waits for the model's own speech to finish, with the mic closed meanwhile", () => {
    const { narrator, creates, mic } = setup();
    narrator.handle({ type: 'response.created', response: { id: 'ack' } });
    narrator.handle({ type: 'output_audio_buffer.started', response_id: 'ack' });
    expect(mic).toEqual([false]);
    narrator.narrate(plan);
    narrator.handle({ type: 'response.done', response: { id: 'ack', status: 'completed', output: [] } });
    expect(creates()).toHaveLength(0);
    narrator.handle({ type: 'output_audio_buffer.stopped', response_id: 'ack' });
    expect(creates()).toHaveLength(1);
  });

  it('cancels and clears audio when stopped mid-sentence, then opens the mic', () => {
    const { narrator, sent, play, mic } = setup();
    narrator.narrate(plan);
    play('r1');
    sent.length = 0;
    narrator.stop();
    expect(sent.map(event => event.type)).toEqual(['response.cancel', 'output_audio_buffer.clear']);
    narrator.handle({ type: 'output_audio_buffer.cleared', response_id: 'r1' });
    vi.advanceTimersByTime(400);
    expect(mic.at(-1)).toBe(true);
  });

  it('calibrates its speaking rate only from plausible measurements', () => {
    const { narrator, play, finish } = setup();
    narrator.narrate(plan); play('r1'); vi.advanceTimersByTime(900); finish('r1');
    expect(narrator.charsPerSecond).toBe(15);  // 29 chars in 0.55 s is implausibly fast
    vi.advanceTimersByTime(400);
    narrator.narrate(plan); play('r2'); vi.advanceTimersByTime(2000); finish('r2');
    expect(narrator.charsPerSecond).toBeCloseTo(15 * 0.7 + (29 / 1.65) * 0.3, 5);
  });

  it('reports invalid tool input and lesson failures to the model instead of narrating', async () => {
    const failing = vi.fn(async () => { throw new Error('Backend returned 409'); });
    const { narrator, sent } = setup(failing);
    for (const [call, args] of [['bad', '{"action":"dance","text":""}'], ['fail', '{"action":"goal","text":"MVT"}']]) {
      narrator.handle({ type: 'response.done', response: { id: call, status: 'completed', output: [{ type: 'function_call', name: 'tutor', call_id: call, arguments: args }] } });
    }
    await vi.waitFor(() => expect(sent.filter(event => event.item?.type === 'function_call_output')).toHaveLength(2));
    const outputs = sent.filter(event => event.item?.type === 'function_call_output').map(event => JSON.parse(event.item.output));
    expect(outputs.every(output => typeof output.error === 'string')).toBe(true);
    expect(sent.filter(event => event.type === 'response.create').every(event => !event.response)).toBe(true);
  });

  it('tells the voice model to listen for an answer', async () => {
    const { narrator, sent } = setup(vi.fn(async () => asking));
    narrator.handle({ type: 'response.done', response: { id: 'r', status: 'completed', output: [{ type: 'function_call', name: 'tutor', call_id: 'c', arguments: '{"action":"goal","text":"dijkstra"}' }] } });
    await vi.waitFor(() => expect(sent.some(event => event.item?.type === 'function_call_output')).toBe(true));
    const output = JSON.parse(sent.find(event => event.item?.type === 'function_call_output').item.output);
    expect(output.awaiting_answer).toBe(true);
    expect(output.note).toContain('call tutor with action answer');
  });

  it('gives an unrequested lesson to the model as context before reading it', () => {
    const { narrator, sent } = setup();
    narrator.introduce(plan);
    expect(sent[0].item.role).toBe('system');
    expect(sent[0].item.content[0].text).toContain('The path costs 11.');
    expect(sent[1].type).toBe('response.create');
  });

  it('speaks a single selected beat and highlights it', () => {
    const { narrator, creates, play, focused } = setup();
    narrator.speakBeat(plan, 1);
    expect(creates()[0].response.instructions).toContain('The path costs 11.');
    expect(creates()[0].response.instructions).not.toContain('Start at A.');
    play('r1');
    expect(focused).toEqual([1]);
  });
});
