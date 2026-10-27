import { typeset } from './math';
import type { SceneNode } from './protocol';

/** A deliberately planar, live illustration of an orthographic shadow. */
export class ShadowStory {
  private drawing: SVGSVGElement;
  private formula: HTMLElement;
  constructor(private host: HTMLElement) {
    host.innerHTML = `<div class="story-heading"><span class="eyebrow">01 / A QUESTION WORTH ASKING</span><h2>How long is a shadow?</h2><p>Shine parallel light toward a wall. Rotate the orange arrow. How much of it lands on the wall?</p></div>
      <svg viewBox="0 0 800 390" role="img" aria-label="A person shining parallel light at an arrow and its shadow on a wall">
        <defs><pattern id="wall-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="#234054" stroke-width="1"/></pattern><marker id="arrow-tip" markerWidth="7" markerHeight="7" refX="5" refY="3" orient="auto"><path d="M0 0L6 3L0 6" fill="#ffba78"/></marker></defs>
        <rect x="645" y="30" width="90" height="300" rx="3" fill="url(#wall-grid)"/>
        <path d="M645 30V330" stroke="#5e8197" stroke-width="2"/>
        <g stroke="#8295ad" stroke-width="5" fill="none" stroke-linecap="round"><circle cx="75" cy="189" r="14"/><path d="M75 205V261M75 224L111 207L142 207M75 227L51 246M75 261L54 300M75 261L98 300"/></g>
        <path d="M136 196L160 190V224L136 218Z" fill="#e3ca80"/>
        <g stroke="#f7df9a" stroke-width="1.5" opacity=".28"><path d="M166 150H638M166 180H638M166 210H638M166 240H638M166 270H638"/></g>
        <path id="story-rays" fill="none" stroke="#ffdf8a" stroke-width="1.5" stroke-dasharray="5 7"/>
        <path id="story-shadow" stroke="#70ead2" stroke-width="9" stroke-linecap="round"/>
        <path id="story-vector" stroke="#ffba78" stroke-width="4" marker-end="url(#arrow-tip)"/>
        <circle cx="425" cy="210" r="5" fill="#edf5ff"/>
        <g fill="#8da6bb" font-family="system-ui" font-size="13"><text x="45" y="336">parallel light</text><text x="383" y="363">rotate the vector →</text><text x="659" y="353">wall</text></g>
      </svg><div class="story-equation" aria-live="off"></div>`;
    this.drawing = host.querySelector('svg')!;
    this.formula = host.querySelector('.story-equation')!;
  }
  update(nodes: SceneNode[], active: boolean) {
    this.host.hidden = !active;
    if (!active) return;
    const u = nodes.find(n => n.id === 'u')?.content;
    const v = nodes.find(n => n.id === 'v')?.content;
    if (u?.primitive !== 'vector_arrow' || v?.primitive !== 'vector_arrow') return;
    const ux = u.end[0] - u.start[0], uy = u.end[1] - u.start[1];
    const norm = Math.hypot(ux, uy);
    if (!norm) return;
    const vx = v.end[0] - v.start[0], vy = v.end[1] - v.start[1];
    // Rotate the coordinate frame so u points upward along the illustrated wall.
    const along = (vx * ux + vy * uy) / norm;
    const across = (vx * uy - vy * ux) / norm;
    const x = 425 + across * 25, y = 210 - along * 25;
    this.drawing.querySelector('#story-vector')!.setAttribute('d', `M425 210L${x} ${y}`);
    this.drawing.querySelector('#story-shadow')!.setAttribute('d', `M645 210V${y}`);
    this.drawing.querySelector('#story-rays')!.setAttribute('d', `M425 210H645M${x} ${y}H645`);
    const equation = String.raw`$s=\frac{\mathbf{u}\cdot\mathbf{v}}{\|\mathbf{u}\|}=${along.toFixed(2)}\qquad \text{shadow length}=|s|=${Math.abs(along).toFixed(2)}$`;
    if (this.formula.dataset.equation !== equation) {
      typeset(this.formula, equation); this.formula.dataset.equation = equation;
    }
  }
}
