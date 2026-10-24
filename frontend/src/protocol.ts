import Ajv2020 from 'ajv/dist/2020';
import eventSchema from '../../shared/schemas/scene.v1.schema.json';
import planSchema from '../../shared/schemas/learning-plan.v1.schema.json';

export type Point = [number, number];
export type Content =
  | { primitive: 'axes2d'; extent: number }
  | { primitive: 'vector_arrow'; start: Point; end: Point }
  | { primitive: 'line_curve'; points: Point[] }
  | { primitive: 'text_label'; text: string; position: Point }
  | { primitive: 'slider'; binding: string; label?: string | null; minimum: number; maximum: number; value: number }
  | { primitive: 'expr_curve'; x: string; y: string; t_min: number; t_max: number; samples: number }
  | { primitive: 'expr_arrow'; start: [string, string]; end: [string, string] }
  | { primitive: 'expr_point'; at: [string, string]; text: string | null };
export interface SceneNode { id: string; content: Content; style: 'primary' | 'secondary' | 'muted'; visible: boolean; coordinate_system: 'lesson_xy' }
export interface Snapshot {
  schema_version: 1; lesson_id: string; scene_id: string; turn_epoch: number;
  seq: number; scene_revision: number; nodes: SceneNode[];
}
export type Operation = { op: 'scene.replace'; nodes: SceneNode[] } | { op: 'node.create' | 'node.patch'; node: SceneNode } | { op: 'node.remove'; object_id: string };
export interface SceneEvent {
  schema_version: 1; event_id: string; lesson_id: string; scene_id: string;
  turn_epoch: number; seq: number; base_revision: number; operation: Operation;
}
export interface TutorState { mode: string; awaiting_answer: boolean; goal: string | null }
export interface Plan { recipe_id: string; planned_by?: string; concept_id?: string | null; generated?: boolean; tutor?: TutorState | null; beats: { beat_id: string; say_text: string; focus_ids: string[] }[] }
const ajv = new Ajv2020({ strict: false, allErrors: false });
const validateEvent = ajv.compile<SceneEvent>(eventSchema);
const validatePlan = ajv.compile<Plan>(planSchema);
const validateSnapshot = ajv.compile<Snapshot>({
  type: 'object', additionalProperties: false,
  $defs: eventSchema.$defs,
  required: ['schema_version', 'lesson_id', 'scene_id', 'turn_epoch', 'seq', 'scene_revision', 'nodes'],
  properties: {
    schema_version: { const: 1 }, lesson_id: { type: 'string', pattern: '^[a-zA-Z0-9_-]{1,80}$' },
    scene_id: { type: 'string', pattern: '^[a-zA-Z0-9_-]{1,80}$' },
    turn_epoch: { type: 'integer', minimum: 0 }, seq: { type: 'integer', minimum: 0 },
    scene_revision: { type: 'integer', minimum: 0 },
    nodes: { type: 'array', maxItems: 30, items: { $ref: '#/$defs/Node' } },
  },
});
function checkNodes(nodes: SceneNode[]) {
  if (nodes.length > 30 || new Set(nodes.map(n => n.id)).size !== nodes.length) throw new Error('Invalid scene object budget or duplicate ID');
  for (const { content: c } of nodes) {
    if (!['axes2d', 'vector_arrow', 'line_curve', 'text_label', 'slider', 'expr_curve', 'expr_arrow', 'expr_point'].includes(c.primitive)) throw new Error('Explicit primitive discriminator required');
    if (c.primitive === 'slider' && (!(c.minimum < c.maximum) || c.value < c.minimum || c.value > c.maximum || (c.binding === 'projection.length' && c.minimum < 0))) {
      throw new Error('Invalid local slider domain');
    }
  }
}
function materializeNodes(nodes: SceneNode[]): SceneNode[] {
  return nodes.map(node => {
    let content = node.content;
    // Pydantic emits defaults in real packets, but the shared schema allows omission.
    if (content.primitive === 'axes2d') content = { ...content, extent: content.extent ?? 5 };
    if (content.primitive === 'vector_arrow') content = { ...content, start: content.start ?? [0, 0] };
    if (content.primitive === 'text_label') content = { ...content, position: content.position ?? [0, 0] };
    if (content.primitive === 'expr_curve') content = { ...content, samples: content.samples ?? 96 };
    if (content.primitive === 'expr_arrow') content = { ...content, start: content.start ?? ['0', '0'] };
    if (content.primitive === 'expr_point') content = { ...content, text: content.text ?? null };
    return { ...node, style: node.style ?? 'primary', visible: node.visible ?? true, coordinate_system: node.coordinate_system ?? 'lesson_xy', content };
  });
}
export function parseSnapshot(value: unknown): Snapshot {
  if (!validateSnapshot(value)) throw new Error('Unsupported scene snapshot');
  checkNodes(value.nodes);
  if (value.scene_id !== `scene_${value.lesson_id}`) throw new Error('Invalid scene scope');
  return structuredClone({ ...value, nodes: materializeNodes(value.nodes) });
}
export function parsePlan(value: unknown): Plan {
  if (!validatePlan(value)) throw new Error('Unsupported teaching plan');
  if ('schema_version' in value && value.schema_version !== 1) throw new Error('Unsupported teaching plan version');
  if (value.beats.length > 32 || value.beats.some(b => b.say_text.length > 4000)) throw new Error('Teaching plan exceeds display budget');
  return value;
}

/** Atomic reducer. A rejected packet never mutates retained state. */
export function applyEvents(state: Snapshot, input: unknown[], epoch: number): Snapshot {
  if (!Number.isSafeInteger(epoch) || epoch !== state.turn_epoch + 1 || input.length > 30) throw new Error('Stale turn; snapshot required');
  let next = structuredClone(state);
  for (const raw of input) {
    if (new TextEncoder().encode(JSON.stringify(raw)).length > 16384 || !validateEvent(raw)) throw new Error('Invalid scene event');
    const event = raw;
    if (event.schema_version !== 1 || !event.operation.op) throw new Error('Explicit event version and operation required');
    if (event.lesson_id !== next.lesson_id || event.scene_id !== next.scene_id || event.turn_epoch !== epoch || event.seq !== next.seq + 1 || event.base_revision !== next.scene_revision) throw new Error('Scene gap; snapshot required');
    const nodes = new Map(next.nodes.map(n => [n.id, n]));
    const op = event.operation;
    if (op.op === 'scene.replace') { checkNodes(op.nodes); nodes.clear(); op.nodes.forEach(n => nodes.set(n.id, n)); }
    else if (op.op === 'node.remove') nodes.delete(op.object_id);
    else {
      const previous = nodes.get(op.node.id);
      if (op.op === 'node.create' ? !!previous : !previous || previous.content.primitive !== op.node.content.primitive) throw new Error('Invalid node lifecycle');
      nodes.set(op.node.id, op.node);
    }
    checkNodes([...nodes.values()]);
    next = { ...next, nodes: materializeNodes([...nodes.values()]), seq: event.seq, scene_revision: next.scene_revision + 1, turn_epoch: epoch };
  }
  if (!input.length) throw new Error('Missing scene event');
  return next;
}

/** Authored recipe only: this never evaluates server expressions or performs network calls. */
export function localProjection(nodes: SceneNode[], angle: number, length?: number): SceneNode[] {
  const result = structuredClone(nodes);
  const u = result.find(n => n.id === 'u')?.content;
  const v = result.find(n => n.id === 'v')?.content;
  const p = result.find(n => n.id === 'projection')?.content;
  if (u?.primitive !== 'vector_arrow' || v?.primitive !== 'vector_arrow' || p?.primitive !== 'vector_arrow') return result;
  const magnitude = length ?? Math.hypot(v.end[0] - v.start[0], v.end[1] - v.start[1]);
  const r = angle * Math.PI / 180;
  const delta: Point = [magnitude * Math.cos(r), magnitude * Math.sin(r)];
  v.end = [v.start[0] + delta[0], v.start[1] + delta[1]];
  const axis: Point = [u.end[0] - u.start[0], u.end[1] - u.start[1]];
  const norm = axis[0] ** 2 + axis[1] ** 2;
  if (!norm) return result;
  const scale = (axis[0] * delta[0] + axis[1] * delta[1]) / norm;
  p.start = [...u.start];
  p.end = [u.start[0] + scale * axis[0], u.start[1] + scale * axis[1]];
  return result;
}
