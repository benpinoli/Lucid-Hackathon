import katex from 'katex';
import 'katex/dist/katex.min.css';

/** Render model-authored math without permitting HTML, URLs, or shared macros. */
export function typeset(host: HTMLElement, text: string) {
  host.replaceChildren();
  const parts = text.split(/(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\])/g);
  for (const part of parts) {
    const math = part.startsWith('$') || part.startsWith('\\(') || part.startsWith('\\[');
    if (!math) { host.append(document.createTextNode(part)); continue; }
    const displayMode = part.startsWith('$$') || part.startsWith('\\[');
    const count = part.startsWith('$$') || part.startsWith('\\') ? 2 : 1;
    const span = document.createElement('span');
    katex.render(part.slice(count, -count), span, { throwOnError: false, trust: false, displayMode, maxExpand: 200, maxSize: 10 });
    host.append(span);
  }
}
