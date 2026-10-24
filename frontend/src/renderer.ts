import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { VRButton } from 'three/addons/webxr/VRButton.js';
import { evaluateSource } from './expressions';
import type { SceneNode } from './protocol';

const CLIP = 7; // Board units; formula geometry beyond this is not drawn (curves break there).

const colors = { primary: 0x71ead5, secondary: 0xf4b477, muted: 0x7f96ab };
function disposeTree(root: THREE.Object3D) {
  root.traverse(object => {
    const mesh = object as THREE.Mesh;
    mesh.geometry?.dispose();
    const materials = mesh.material ? (Array.isArray(mesh.material) ? mesh.material : [mesh.material]) : [];
    for (const material of materials) {
      (material as THREE.MeshBasicMaterial).map?.dispose(); material.dispose();
    }
  });
  root.clear();
}
function label(text: string, color: string, width = 2.4, height = 0.26, fit = false) {
  const canvas = document.createElement('canvas'); canvas.height = 256;
  const ctx = canvas.getContext('2d')!;
  // Fit shrinks the backing plate to the text so small labels do not cover neighbours; width stays the cap.
  if (fit) { ctx.font = '160px system-ui'; width = Math.min(width, height * (ctx.measureText(text).width + 96) / 256); }
  canvas.width = Math.min(4096, Math.ceil(256 * width / height));
  ctx.fillStyle = '#132630'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = color; ctx.font = '160px system-ui'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(text, canvas.width / 2, canvas.height / 2, canvas.width - 24);
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false }));
  sprite.scale.set(width, height, 1); return sprite;
}

export class LessonRenderer {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(48, 1, 0.05, 100);
  private controls: OrbitControls;
  private board = new THREE.Group();
  private caption = new THREE.Group();
  private xrButtons = new THREE.Group();
  private observer: ResizeObserver;
  private vrButton: HTMLElement;
  private frameTimes: number[] = [];
  private lastFrame = 0;
  private lastReport = 0;
  private raycaster = new THREE.Raycaster();
  private rotation = new THREE.Matrix4();
  private focus = new Set<string>();
  private nodes: SceneNode[] = [];
  private params: Record<string, number> = {};
  private marker = new THREE.Group();
  private xrActions: ((direction: number) => void) | undefined;
  private xrInterrupt: (() => void) | undefined;

  constructor(host: HTMLElement, xrHost: HTMLElement, private metrics: (text: string) => void, onError: (text: string) => void) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.xr.enabled = true;
    this.renderer.xr.setReferenceSpaceType('local-floor');
    this.renderer.setClearColor(0x101f2a);
    this.renderer.domElement.setAttribute('aria-label', 'Interactive lesson scene. Drag to orbit; scroll to zoom.');
    this.renderer.domElement.addEventListener('webglcontextlost', event => { event.preventDefault(); onError('Graphics context lost. Reload this page to restore rendering.'); });
    host.append(this.renderer.domElement);
    this.camera.position.set(0, 1.6, 1.1);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.set(0, 1.45, -2.5); this.controls.enableDamping = true;
    this.controls.minDistance = 1; this.controls.maxDistance = 12;
    this.board.position.set(0, 1.45, -2.5); this.board.scale.setScalar(0.23);
    this.scene.add(this.board, this.caption, this.xrButtons);
    this.board.add(this.marker);
    const floor = new THREE.GridHelper(20, 40, 0x294653, 0x1b303e); floor.position.y = -0.01; this.scene.add(floor);
    this.caption.position.set(0, 0.48, -2.5);
    this.xrButtons.position.set(0, 0.85, -2.5);
    for (const direction of [-1, 1]) {
      const button = new THREE.Mesh(new THREE.PlaneGeometry(0.6, 0.18), new THREE.MeshBasicMaterial({ color: 0x284e5d, side: THREE.DoubleSide }));
      button.position.x = direction * 0.4; button.userData.direction = direction;
      const text = label(direction < 0 ? '− angle' : '+ angle', '#e4fff9', 0.54, 0.14); text.position.z = 0.01; button.add(text);
      this.xrButtons.add(button);
    }
    this.xrButtons.visible = false;
    for (let i = 0; i < 2; i++) {
      const controller = this.renderer.xr.getController(i);
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, -4)]), new THREE.LineBasicMaterial({ color: 0x71ead5 }));
      controller.add(line); this.scene.add(controller);
      controller.addEventListener('select', () => {
        this.rotation.extractRotation(controller.matrixWorld);
        this.raycaster.ray.origin.setFromMatrixPosition(controller.matrixWorld);
        this.raycaster.ray.direction.set(0, 0, -1).applyMatrix4(this.rotation);
        const hit = this.xrButtons.visible ? this.raycaster.intersectObjects(this.xrButtons.children, false)[0] : undefined;
        if (hit) this.xrActions?.(hit.object.userData.direction);
        else this.xrInterrupt?.();
      });
    }
    this.vrButton = VRButton.createButton(this.renderer);
    // Override VRButton's viewport positioning so it belongs to the scene toolbar.
    this.vrButton.style.cssText += ';position:static;width:auto;bottom:auto;left:auto;transform:none;margin:0;';
    xrHost.append(this.vrButton);
    this.renderer.xr.addEventListener('sessionstart', () => { this.controls.enabled = false; this.xrButtons.visible = !!this.xrActions; });
    this.renderer.xr.addEventListener('sessionend', () => { this.controls.enabled = true; this.xrButtons.visible = false; this.resetView(); });
    this.observer = new ResizeObserver(() => {
      const { width, height } = host.getBoundingClientRect();
      if (!width || !height) return;
      this.camera.aspect = width / height; this.camera.updateProjectionMatrix();
      this.renderer.setSize(width, height);
    });
    this.observer.observe(host);
    this.renderer.setAnimationLoop(time => {
      if (this.lastFrame && time > this.lastFrame) { this.frameTimes.push(time - this.lastFrame); if (this.frameTimes.length > 120) this.frameTimes.shift(); }
      this.lastFrame = time;
      this.controls.update(); this.renderer.render(this.scene, this.camera);
      if (time - this.lastReport > 1000 && this.frameTimes.length) {
        const sorted = [...this.frameTimes].sort((a, b) => a - b);
        this.metrics(`${this.renderer.xr.isPresenting ? 'XR' : 'Desktop'} · frame p95 ${sorted[Math.floor((sorted.length - 1) * 0.95)].toFixed(1)} ms · ${this.renderer.info.memory.geometries} geometries · ${this.renderer.info.memory.textures} textures`);
        this.lastReport = time;
      }
    });
  }
  resetView() { this.camera.position.set(0, 1.6, 1.1); this.controls.target.set(0, 1.45, -2.5); this.controls.update(); }
  setFocus(ids: string[]) { this.focus = new Set(ids); this.render(this.nodes, this.params); }
  setXRInterrupt(action?: () => void) { this.xrInterrupt = action; }
  /** Quiet provenance marker in the board's top-right corner, visible in VR too. */
  setMarker(text?: string) {
    disposeTree(this.marker);
    if (!text) return;
    const sprite = label(text, '#7f96ab', 3, 0.42, true); sprite.position.set(4.6, 5.3, 0.1); this.marker.add(sprite);
  }
  setCaption(text: string) {
    disposeTree(this.caption);
    // Two legible lines in VR; full accessible text remains in the DOM.
    const words = text.split(' '); const lines = [''];
    for (const word of words) { if ((lines.at(-1)!.length + word.length) > 65 && lines.length < 2) lines.push(''); lines[lines.length - 1] += word + ' '; }
    lines.forEach((line, i) => { const sprite = label(line.slice(0, 100), '#e2edf1', 2.8, 0.16); sprite.position.y = -i * 0.18; this.caption.add(sprite); });
  }
  setXRSlider(action?: (direction: number) => void, name = 'angle') {
    this.xrActions = action; this.xrButtons.visible = !!action && this.renderer.xr.isPresenting;
    if (action && this.xrButtons.userData.name !== name) {
      this.xrButtons.userData.name = name;
      for (const button of this.xrButtons.children as THREE.Mesh[]) {
        const old = button.children[0]; button.remove(old); disposeTree(old);
        const text = label(`${button.userData.direction < 0 ? '−' : '+'} ${name}`.slice(0, 18), '#e4fff9', 0.54, 0.14); text.position.z = 0.01; button.add(text);
      }
    }
  }
  /** Rebuilds the board. Formula primitives are evaluated with `params` (slider values). */
  render(nodes: SceneNode[], params: Record<string, number> = {}) {
    this.nodes = nodes; this.params = params;
    this.board.remove(this.marker); disposeTree(this.board); this.board.add(this.marker);
    for (const node of nodes) {
      if (!node.visible) continue;
      const group = new THREE.Group(); group.name = node.id;
      const color = colors[node.style]; const c = node.content;
      const line = (points: number[][], tint = color) => {
        const geometry = new THREE.BufferGeometry().setFromPoints(points.map(([x, y]) => new THREE.Vector3(x, y, 0)));
        group.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: tint })));
      };
      if (c.primitive === 'axes2d') {
        for (let x = -c.extent; x <= c.extent; x++) line([[x, -c.extent], [x, c.extent]], 0x223c49);
        for (let y = -c.extent; y <= c.extent; y++) line([[-c.extent, y], [c.extent, y]], 0x223c49);
        line([[-c.extent, 0], [c.extent, 0]], 0x557a8a); line([[0, -c.extent], [0, c.extent]], 0x557a8a);
      } else if (c.primitive === 'vector_arrow') {
        const start = new THREE.Vector3(...c.start, node.id === 'projection' ? 0.04 : 0.02);
        const end = new THREE.Vector3(...c.end, start.z); const delta = end.clone().sub(start); const size = delta.length();
        if (size > 0.00001) group.add(new THREE.ArrowHelper(delta.normalize(), start, size, color, Math.min(0.3, size * 0.3), Math.min(0.17, size * 0.15)));
        const name = label(node.id, new THREE.Color(color).getStyle(), node.id === 'projection' ? 2.2 : 1, 0.7);
        name.position.copy(end).add(new THREE.Vector3(0.25, node.id === 'projection' ? -0.4 : 0.4, 0.08)); group.add(name);
      } else if (c.primitive === 'line_curve') line(c.points);
      else if (c.primitive === 'expr_curve') {
        // Split into runs at non-finite or clipped samples so asymptotes are never bridged.
        const runs: number[][][] = [[]];
        for (let i = 0; i < c.samples; i++) {
          const scope = { ...params, t: c.t_min + (c.t_max - c.t_min) * i / (c.samples - 1) };
          const x = evaluateSource(c.x, scope), y = evaluateSource(c.y, scope);
          if (Number.isFinite(x) && Number.isFinite(y) && Math.abs(x) <= CLIP && Math.abs(y) <= CLIP) runs[runs.length - 1].push([x, y]);
          else if (runs[runs.length - 1].length) runs.push([]);
        }
        for (const run of runs) if (run.length > 1) line(run);
      } else if (c.primitive === 'expr_arrow') {
        const [sx, sy, ex, ey] = [...c.start, ...c.end].map(source => evaluateSource(source, params));
        if ([sx, sy, ex, ey].every(v => Number.isFinite(v) && Math.abs(v) <= CLIP)) {
          const start = new THREE.Vector3(sx, sy, 0.02); const delta = new THREE.Vector3(ex - sx, ey - sy, 0); const size = delta.length();
          if (size > 0.00001) group.add(new THREE.ArrowHelper(delta.normalize(), start, size, color, Math.min(0.3, size * 0.3), Math.min(0.17, size * 0.15)));
        }
      } else if (c.primitive === 'expr_point') {
        const [x, y] = c.at.map(source => evaluateSource(source, params));
        if (Number.isFinite(x) && Number.isFinite(y) && Math.abs(x) <= CLIP && Math.abs(y) <= CLIP) {
          line(Array.from({ length: 13 }, (_, i) => [x + 0.13 * Math.cos(i * Math.PI / 6), y + 0.13 * Math.sin(i * Math.PI / 6)]), 0xf4b477);
          if (c.text) { const sprite = label(c.text, '#d5e8ee', 6, 0.5, true); sprite.position.set(x + 0.35, y + 0.45, 0.1); group.add(sprite); }
        }
      }
      else if (c.primitive === 'text_label') {
        const sprite = label(c.text, '#d5e8ee', 9, 0.7, true);
        sprite.position.set(c.position[0], c.position[1] + (node.id === 'formula' ? -4.2 : 0), 0.1); group.add(sprite);
      }
      if (this.focus.size && !this.focus.has(node.id)) group.traverse(object => {
        const material = (object as THREE.Mesh).material;
        if (material && !Array.isArray(material)) { material.transparent = true; material.opacity = 0.4; }
      });
      this.board.add(group);
    }
  }
  dispose() {
    this.observer.disconnect(); this.renderer.setAnimationLoop(null); this.controls.dispose();
    void this.renderer.xr.getSession()?.end(); disposeTree(this.scene); this.renderer.dispose();
    this.renderer.domElement.remove(); this.vrButton.remove();
  }
}
