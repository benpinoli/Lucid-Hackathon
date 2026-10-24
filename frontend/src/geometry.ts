import { evaluateSource } from './expressions';
import type { Content } from './protocol';

/** Bounded mesh sampling. Invalid/clipped vertices never participate in triangles. */
export function sampleSurface(c: Extract<Content, { primitive: 'expr_surface' }>, params: Record<string, number> = {}) {
  const positions: number[] = [], indices: number[] = [], valid: boolean[] = [];
  const n = c.samples;
  if (!Number.isInteger(n) || n < 8 || n > 40) throw new Error('Invalid surface sampling budget');
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
    const scope = { ...params, u: c.u_min + (c.u_max - c.u_min) * i / (n - 1), v: c.v_min + (c.v_max - c.v_min) * j / (n - 1) };
    const point = [c.x, c.y, c.z].map(expr => evaluateSource(expr, scope));
    const inside = point.every(v => Number.isFinite(v) && Math.abs(v) <= 7);
    valid.push(inside); positions.push(...(inside ? point : [0, 0, 0]));
  }
  const triangle = (a: number, b: number, d: number) => {
    if (valid[a] && valid[b] && valid[d]) indices.push(a, b, d);
  };
  for (let i = 0; i < n - 1; i++) for (let j = 0; j < n - 1; j++) {
    const a = i * n + j, b = a + n;
    triangle(a, b, a + 1); triangle(a + 1, b, b + 1);
  }
  return { positions, indices };
}
