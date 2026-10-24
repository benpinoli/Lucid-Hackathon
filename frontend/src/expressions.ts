/**
 * Safe arithmetic expressions for diagrams; mirrors backend/app/expressions.py.
 * No eval: a bounded recursive-descent parser over an allowlisted grammar. Domain errors
 * evaluate to NaN. Both implementations are checked against shared/fixtures/expressions.json.
 */
type Node =
  | { k: 'num'; v: number } | { k: 'var'; v: string } | { k: 'neg'; a: Node }
  | { k: 'bin'; op: string; a: Node; b: Node } | { k: 'call'; f: string; args: Node[] };
type Token = ['num', number] | ['name', string] | ['op', string];

const MAX_LENGTH = 160;
const MAX_DEPTH = 24;
const FUNCTIONS: Record<string, [number, (...x: number[]) => number]> = {
  sin: [1, Math.sin], cos: [1, Math.cos], tan: [1, Math.tan], asin: [1, Math.asin], acos: [1, Math.acos],
  atan: [1, Math.atan], exp: [1, Math.exp], ln: [1, Math.log], sqrt: [1, Math.sqrt], abs: [1, Math.abs],
  floor: [1, Math.floor], min: [2, Math.min], max: [2, Math.max],
};
const CONSTANTS: Record<string, number> = { pi: Math.PI, e: Math.E };

function tokens(source: string): Token[] {
  if (typeof source !== 'string' || !source.trim() || source.length > MAX_LENGTH) throw new Error(`Expressions must be 1-${MAX_LENGTH} characters`);
  const out: Token[] = [];
  const pattern = /\s*(?:(\d+\.?\d*|\.\d+)|([a-z]+)|(.))/gy;
  let match: RegExpExecArray | null;
  while (pattern.lastIndex < source.length && (match = pattern.exec(source))) {
    if (match[1]) out.push(['num', Number(match[1])]);
    else if (match[2]) out.push(['name', match[2]]);
    else if (match[3] && '+-*/^(),'.includes(match[3])) out.push(['op', match[3]]);
    else if (match[3]?.trim()) throw new Error(`Unexpected character ${match[3]}`);
  }
  return out;
}

export function parse(source: string): Node {
  const list = tokens(source);
  let index = 0; let depth = 0;
  const peek = (): Token | undefined => list[index];
  const isOp = (...ops: string[]) => { const t = peek(); return !!t && t[0] === 'op' && ops.includes(t[1] as string); };
  const take = (value?: string): Token => {
    const t = peek();
    if (!t || (value !== undefined && t[1] !== value)) throw new Error(`Expected ${value ?? 'more input'}`);
    index++; return t;
  };
  const nest = () => { if (++depth > MAX_DEPTH) throw new Error('Expression is nested too deeply'); };
  const expr = (): Node => {
    nest(); let node = term();
    while (isOp('+', '-')) { const op = take()[1] as string; node = { k: 'bin', op, a: node, b: term() }; }
    depth--; return node;
  };
  const term = (): Node => {
    let node = unary();
    while (isOp('*', '/')) { const op = take()[1] as string; node = { k: 'bin', op, a: node, b: unary() }; }
    return node;
  };
  const unary = (): Node => {
    if (isOp('+', '-')) { nest(); const sign = take()[1]; const node = unary(); depth--; return sign === '+' ? node : { k: 'neg', a: node }; }
    return power();
  };
  const power = (): Node => {
    let node = primary();
    if (isOp('^')) { take(); nest(); node = { k: 'bin', op: '^', a: node, b: unary() }; depth--; }
    return node;
  };
  const primary = (): Node => {
    const [kind, value] = take();
    if (kind === 'num') return { k: 'num', v: value as number };
    if (kind === 'name') {
      const name = value as string;
      if (name in FUNCTIONS) {
        take('('); const args = [expr()];
        while (isOp(',')) { take(); args.push(expr()); }
        take(')');
        if (args.length !== FUNCTIONS[name][0]) throw new Error(`${name} takes ${FUNCTIONS[name][0]} argument(s)`);
        return { k: 'call', f: name, args };
      }
      if (name in CONSTANTS) return { k: 'num', v: CONSTANTS[name] };
      if (name.length === 1) return { k: 'var', v: name };
      throw new Error(`Unknown name ${name}`);
    }
    if (value === '(') { const node = expr(); take(')'); return node; }
    throw new Error(`Unexpected ${value}`);
  };
  const node = expr();
  if (index !== list.length) throw new Error('Unexpected trailing input');
  return node;
}

function divide(a: number, b: number) {
  if (b === 0) return a === 0 || Number.isNaN(a) ? NaN : (Math.sign(a) * (Object.is(b, -0) ? -1 : 1)) * Infinity;
  return a / b;
}

export function evaluate(node: Node, scope: Record<string, number>): number {
  switch (node.k) {
    case 'num': return node.v;
    case 'var': return scope[node.v] ?? NaN;
    case 'neg': return -evaluate(node.a, scope);
    case 'call': return FUNCTIONS[node.f][1](...node.args.map(arg => evaluate(arg, scope)));
    case 'bin': {
      const a = evaluate(node.a, scope), b = evaluate(node.b, scope);
      if (node.op === '+') return a + b;
      if (node.op === '-') return a - b;
      if (node.op === '*') return a * b;
      if (node.op === '/') return divide(a, b);
      // Python's math.pow rejects a negative base with a fractional exponent; Math.pow gives NaN too.
      return Math.pow(a, b);
    }
  }
}

const cache = new Map<string, Node | null>();
/** Parse with a small cache; an invalid expression evaluates to NaN rather than throwing. */
export function evaluateSource(source: string, scope: Record<string, number>): number {
  if (!cache.has(source)) {
    if (cache.size > 512) cache.clear();
    try { cache.set(source, parse(source)); } catch { cache.set(source, null); }
  }
  const node = cache.get(source);
  return node ? evaluate(node, scope) : NaN;
}
