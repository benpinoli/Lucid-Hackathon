import { Object3D } from 'three';

/** Attach preserves world transforms. A controller owns at most one object; no shared ownership. */
export class GrabManager {
  private held = new Map<Object3D, { object: Object3D; parent: Object3D }>();
  begin(controller: Object3D, object: Object3D) {
    if (!object.parent || this.held.has(controller) || [...this.held.values()].some(h =>
      h.object === object || this.contains(h.object, object) || this.contains(object, h.object))) return false;
    const parent = object.parent;
    controller.updateWorldMatrix(true, false); object.updateWorldMatrix(true, false);
    controller.attach(object);
    this.held.set(controller, { object, parent });
    return true;
  }
  release(controller: Object3D) {
    const held = this.held.get(controller);
    if (!held) return;
    held.parent.updateWorldMatrix(true, false); held.object.updateWorldMatrix(true, false);
    held.parent.attach(held.object); this.held.delete(controller);
  }
  releaseAll() { for (const controller of [...this.held.keys()]) this.release(controller); }
  entries() { return [...this.held].map(([controller, held]) => ({ controller, object: held.object })); }
  private contains(parent: Object3D, child: Object3D): boolean {
    for (let current: Object3D | null = child; current; current = current.parent) if (current === parent) return true;
    return false;
  }
}
