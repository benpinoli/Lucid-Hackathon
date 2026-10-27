import * as THREE from 'three';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { GLTFLoader, type GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { clone } from 'three/addons/utils/SkeletonUtils.js';

export interface ModelInfo { id: string; name: string; license: string; credit: string; clips: string[]; ready: boolean }
export interface ModelInstance { object: THREE.Object3D; clips: THREE.AnimationClip[] }

/**
 * Catalog models are parsed once and cloned per scene object, so a lesson that needs one
 * shows it immediately after the first download. Bytes come only from the LUCID backend
 * (same-origin, sha256-pinned); the Draco decoder is served by Vite from three's own files.
 */
export class ModelCache {
  private loader = new GLTFLoader().setDRACOLoader(new DRACOLoader().setDecoderPath('/draco/'));
  private parsed = new Map<string, Promise<GLTF>>();
  catalog = new Map<string, ModelInfo>();

  constructor(private download: (path: string) => Promise<ArrayBuffer>) {}

  private load(id: string) {
    let gltf = this.parsed.get(id);
    if (!gltf) {
      gltf = this.download(`/api/models/${encodeURIComponent(id)}.glb`).then(bytes => this.loader.parseAsync(bytes, ''));
      // A failed download is retried next time rather than cached.
      gltf.catch(() => { if (this.parsed.get(id) === gltf) this.parsed.delete(id); });
      this.parsed.set(id, gltf);
    }
    return gltf;
  }

  /** A private copy: geometry and textures are shared, materials are per instance so focus dimming stays local. */
  async instance(id: string): Promise<ModelInstance> {
    const gltf = await this.load(id);
    const object = clone(gltf.scene);
    object.traverse(child => {
      const mesh = child as THREE.Mesh;
      if (mesh.material) mesh.material = Array.isArray(mesh.material) ? mesh.material.map(m => m.clone()) : mesh.material.clone();
      child.userData.lucidShared = true;
    });
    return { object, clips: gltf.animations };
  }

  setCatalog(models: ModelInfo[]) { this.catalog = new Map(models.map(model => [model.id, model])); }

  /** Download and parse every catalog model in the background, one at a time. */
  async warm() {
    for (const id of this.catalog.keys()) await this.load(id).catch(() => undefined);
  }

  credits(ids: Iterable<string>) {
    return [...new Set(ids)].map(id => this.catalog.get(id)).filter((m): m is ModelInfo => !!m)
      .map(m => `${m.name}: ${m.credit} (${m.license})`);
  }

  clear() { this.parsed.clear(); this.catalog.clear(); }
}
