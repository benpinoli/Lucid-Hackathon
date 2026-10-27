import { describe, expect, it, vi } from 'vitest';
import { RealtimeTeacher } from '../src/realtime';

function setup(tool = vi.fn(async (_name: string, _args: object, _id: string): Promise<Record<string, unknown>> => ({ speak_text: 'Watch the tangent turn.' }))) {
  const sent: Record<string, any>[] = [];
  const focus = vi.fn(), mic = vi.fn(), transcript = vi.fn(), interrupt = vi.fn(async () => undefined);
  const teacher = new RealtimeTeacher({ send: event => sent.push(event), tool, focusBeat: focus,
    setMicOpen: mic, transcript, interrupt, status: vi.fn() });
  const done = (name: string, args: object, id = 'call_one', responseId = 'r1') => teacher.handle({
    type: 'response.done', response: { id: responseId, status: 'completed', output: [{
      type: 'function_call', name, call_id: id, arguments: JSON.stringify(args),
    }] },
  });
  const creates = () => sent.filter(e => e.type === 'response.create');
  return { teacher, sent, focus, mic, transcript, tool, interrupt, done, creates };
}

describe('single Realtime teacher', () => {
  it('executes scene code locally then speaks one step when playback starts', async () => {
    const { teacher, tool, sent, creates, done, focus } = setup();
    teacher.handle({ type: 'response.created', response: { id: 'r1' } });
    teacher.handle({ type: 'output_audio_buffer.started', response_id: 'r1' });
    done('present_lesson', { lesson: { title: 'Slope' } });
    await vi.waitFor(() => expect(tool).toHaveBeenCalledTimes(1));
    expect(tool).toHaveBeenCalledWith('present_lesson', { lesson: { title: 'Slope' } }, 'call_one');
    expect(creates()).toHaveLength(0); // The initial spoken preamble is still playing.
    teacher.handle({ type: 'output_audio_buffer.stopped', response_id: 'r1' });
    await vi.waitFor(() => expect(creates()).toHaveLength(1));
    expect(creates()[0].response.instructions).toContain('Watch the tangent turn.');
    expect(creates()[0].response.tool_choice).toBe('none');
    expect(sent.find(e => e.item?.type === 'function_call_output')!.item.call_id).toBe('call_one');
    expect(focus).not.toHaveBeenCalled();
    teacher.handle({ type: 'output_audio_buffer.started', response_id: 'step1' });
    expect(focus).toHaveBeenCalledWith(0);
    teacher.stop();
  });

  it('lets the same model repair invalid code without calling a second model', async () => {
    const tool = vi.fn(async () => { throw new Error('Unknown slider p. Add its parameter first.'); });
    const { done, creates, sent } = setup(tool);
    done('present_lesson', {});
    await vi.waitFor(() => expect(creates()).toHaveLength(1));
    expect(JSON.parse(sent.find(e => e.item?.type === 'function_call_output')!.item.output).error).toContain('Unknown slider');
    expect(creates()[0].response.instructions).toBeUndefined();
  });

  it('ignores replayed function calls and canceled response completions', async () => {
    const { teacher, done, tool } = setup();
    done('get_learning_context', { topic: 'derivatives' });
    await vi.waitFor(() => expect(tool).toHaveBeenCalledTimes(1));
    done('get_learning_context', { topic: 'derivatives' });
    await Promise.resolve();
    expect(tool).toHaveBeenCalledTimes(1);
    teacher.handle({ type: 'response.created', response: { id: 'old' } });
    teacher.stop();
    done('remember_concepts', {}, 'late_call', 'old');
    await Promise.resolve();
    expect(tool).toHaveBeenCalledTimes(1);
  });

  it('does not resume a late tool result after interruption', async () => {
    let resolve!: (result: Record<string, unknown>) => void;
    const tool = vi.fn(() => new Promise<Record<string, unknown>>(r => { resolve = r; }));
    const { teacher, done, creates, sent, interrupt } = setup(tool);
    done('present_lesson', {});
    await vi.waitFor(() => expect(tool).toHaveBeenCalledTimes(1));
    teacher.stop();
    resolve({ speak_text: 'A canceled step.' });
    await new Promise(r => setTimeout(r, 0));
    expect(interrupt).toHaveBeenCalledTimes(1);
    expect(creates()).toHaveLength(0);
    expect(sent.filter(e => e.item?.type === 'function_call_output')).toHaveLength(0);
  });

  it('routes typed turns through the same agent with fresh context and text output', async () => {
    const { teacher, tool, creates, sent, transcript } = setup(vi.fn(async () => ({ learner_notes: 'Use geometric examples.' })));
    teacher.configure(false);
    await teacher.request('explore', 'Teach me Fourier series.', false);
    expect(tool).toHaveBeenCalledWith('get_learning_context', { topic: '', learner_text: 'Teach me Fourier series.' }, expect.any(String));
    expect(sent.find(e => e.item?.role === 'system')!.item.content[0].text).toContain('Use geometric examples');
    expect(sent.find(e => e.item?.role === 'user')!.item.content[0].text).toContain('Fourier');
    expect(creates()[0].response.output_modalities).toEqual(['text']);
    teacher.handle({ type: 'response.output_text.delta', delta: 'Imagine ' });
    teacher.handle({ type: 'response.output_text.delta', delta: 'a vibrating string.' });
    expect(transcript).toHaveBeenLastCalledWith('Imagine a vibrating string.');
    teacher.stop();
  });
});
