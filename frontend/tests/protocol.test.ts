import { describe, expect, it } from 'vitest';
import { applyEvents, localProjection, parsePlan, parseSnapshot, type SceneNode, type Snapshot } from '../src/protocol';

const node: SceneNode = { id: 'u', style: 'primary', visible: true, coordinate_system: 'lesson_xy', content: { primitive: 'vector_arrow', start: [0, 0], end: [3, 0] } };
const state: Snapshot = { schema_version: 1, lesson_id: 'lesson', scene_id: 'scene_lesson', turn_epoch: 0, seq: 0, scene_revision: 0, nodes: [] };
const event = { schema_version: 1, event_id: 'event1', lesson_id: 'lesson', scene_id: 'scene_lesson', turn_id: 'turn', utterance_id: 'utterance', turn_epoch: 1, seq: 1, base_revision: 0, cue: 'immediate', operation: { op: 'scene.replace', nodes: [node] } };
describe('shared contract and atomic scene state', () => {
  it('accepts a backend-shaped event and snapshot without mutating inputs', () => {
    const next = applyEvents(state, [event], 1);
    expect(next.nodes).toEqual([node]); expect(next.scene_revision).toBe(1);
    expect(state.nodes).toEqual([]); expect(parseSnapshot(next)).toEqual(next);
  });
  it.each([
    { seq: 2 }, { base_revision: 1 }, { turn_epoch: 2 }, { lesson_id: 'other' },
    { scene_id: 'other' }, { schema_version: 2 }, { arbitrary_code: 'bad' },
    { operation: { op: 'scene.replace', nodes: [node, node] } },
    { operation: { op: 'node.patch', node } },
    { operation: { op: 'scene.replace', nodes: [{ ...node, content: { primitive: 'remote_asset', url: 'https://example.com' } }] } },
  ])('rejects gaps, wrong scopes, unknown content and invalid lifecycle: %j', patch => {
    expect(() => applyEvents(state, [{ ...event, ...patch }], 1)).toThrow();
    expect(state.nodes).toEqual([]);
  });
  it('rejects duplicate replay and stale epochs', () => {
    const next = applyEvents(state, [event], 1);
    expect(() => applyEvents(next, [event], 1)).toThrow();
  });
  it('materializes schema-optional renderer defaults', () => {
    const snapshot = parseSnapshot({ ...state, nodes: [{ id: 'arrow', content: { primitive: 'vector_arrow', end: [2, 3] } }] });
    expect(snapshot.nodes[0]).toEqual({ id: 'arrow', style: 'primary', visible: true, coordinate_system: 'lesson_xy', content: { primitive: 'vector_arrow', start: [0, 0], end: [2, 3] } });
  });
  it('validates slider domains beyond JSON Schema constraints', () => {
    const slider = { ...node, content: { primitive: 'slider', binding: 'projection.angle', minimum: 10, maximum: 0, value: 5 } };
    expect(() => parseSnapshot({ ...state, nodes: [slider] })).toThrow();
  });
  it('rejects nonfinite numbers, duplicate snapshot objects, and unsupported plans', () => {
    expect(() => parseSnapshot({ ...state, nodes: [node, node] })).toThrow();
    expect(() => parseSnapshot({ ...state, nodes: [{ ...node, content: { ...node.content, end: [Infinity, 0] } }] })).toThrow();
    expect(() => parsePlan({ recipe_id: 'bad', beats: [], code: 'run' })).toThrow();
  });
  it('keeps 500 replacements bounded and revision-consistent', () => {
    let current = state;
    for (let i = 1; i <= 500; i++) current = applyEvents(current, [{ ...event, event_id: `event${i}`, turn_epoch: i, seq: i, base_revision: i - 1 }], i);
    expect(current.nodes.length).toBe(1); expect(current.scene_revision).toBe(500);
  });
});
describe('local projection', () => {
  const nodes: SceneNode[] = [node, { ...node, id: 'v', content: { primitive: 'vector_arrow', start: [0, 0], end: [2, 3] } }, { ...node, id: 'projection', content: { primitive: 'vector_arrow', start: [0, 0], end: [2, 0] } }];
  it('projects at right angles, negative angles, and new magnitudes', () => {
    const output = localProjection(nodes, 90, 4);
    const p = output[2].content;
    if (p.primitive !== 'vector_arrow') throw new Error();
    expect(p.end[0]).toBeCloseTo(0); expect(p.end[1]).toBe(0);
    const negative = localProjection(nodes, 180, 4)[2].content;
    if (negative.primitive !== 'vector_arrow') throw new Error();
    expect(negative.end[0]).toBeCloseTo(-4); expect(nodes[2].content).toEqual({ primitive: 'vector_arrow', start: [0, 0], end: [2, 0] });
  });
});
