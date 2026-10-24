import { describe, expect, it } from 'vitest';
import { Group, Object3D, Vector3 } from 'three';
import { sampleSurface } from '../src/geometry';
import { GrabManager } from '../src/manipulation';
import { parseSnapshot } from '../src/protocol';

describe('genuine 3D geometry', () => {
  const surface = { primitive: 'expr_surface' as const, x: 'u', y: 'v', z: 'u^2-v^2+1',
    u_min: -1, u_max: 1, v_min: -1, v_max: 1, samples: 8 };
  it('triangulates a nonplanar surface with bounded indices', () => {
    const mesh = sampleSurface(surface);
    expect(mesh.positions.length).toBe(8 * 8 * 3);
    expect(mesh.indices.length).toBe(7 * 7 * 6);
    expect(Math.max(...mesh.indices)).toBeLessThan(64);
    expect(new Set(mesh.positions.filter((_, i) => i % 3 === 2)).size).toBeGreaterThan(2);
  });
  it('never bridges undefined regions with surface triangles', () => {
    const mesh = sampleSurface({ ...surface, z: 'sqrt(u)' });
    expect(mesh.positions.every(Number.isFinite)).toBe(true);
    for (const index of mesh.indices) expect(mesh.positions[index * 3]).toBeGreaterThanOrEqual(0);
    expect(() => sampleSurface({ ...surface, samples: 1000 })).toThrow();
  });
  it('accepts true 3D packets and rejects mixed arrow dimensions', () => {
    const snapshot = { schema_version: 1, lesson_id: 'lesson', scene_id: 'scene_lesson', turn_epoch: 0,
      seq: 0, scene_revision: 0, nodes: [{ id: 'surface', coordinate_system: 'lesson_xyz', content: surface }] };
    expect(parseSnapshot(snapshot).nodes[0].coordinate_system).toBe('lesson_xyz');
    expect(() => parseSnapshot({ ...snapshot, nodes: [{ id: 'bad', content: {
      primitive: 'vector_arrow', start: [0, 0], end: [1, 2, 3] } }] })).toThrow();
  });
});

describe('VR grab transforms', () => {
  it('preserves placement at pickup, follows controller position and rotation, and releases in place', () => {
    const scene = new Group(), board = new Group(), controller = new Group(), object = new Object3D();
    scene.add(board, controller); board.position.set(0, 1, -3); board.scale.setScalar(0.23);
    board.add(object); object.position.set(1, 0, 1); scene.updateMatrixWorld(true);
    const before = object.getWorldPosition(new Vector3());
    const grabs = new GrabManager();
    expect(grabs.begin(controller, object)).toBe(true);
    expect(object.getWorldPosition(new Vector3()).distanceTo(before)).toBeLessThan(1e-9);
    controller.position.x += 1; controller.rotation.y = Math.PI / 4; scene.updateMatrixWorld(true);
    const moved = object.getWorldPosition(new Vector3());
    expect(moved.distanceTo(before)).toBeGreaterThan(0.5);
    grabs.release(controller);
    expect(object.parent).toBe(board);
    expect(object.getWorldPosition(new Vector3()).distanceTo(moved)).toBeLessThan(1e-9);
  });
  it('prevents conflicting grips and releases everything on disconnect', () => {
    const scene = new Group(), board = new Group(), a = new Group(), b = new Group(), object = new Group();
    scene.add(board, a, b); board.add(object);
    const grabs = new GrabManager();
    expect(grabs.begin(a, board)).toBe(true);
    expect(grabs.begin(b, object)).toBe(false);
    expect(grabs.begin(b, board)).toBe(false);
    grabs.releaseAll(); expect(board.parent).toBe(scene);
    expect(grabs.begin(b, object)).toBe(true);
    grabs.releaseAll(); expect(object.parent).toBe(board);
  });
});
