import { describe, expect, it } from 'vitest';
import fixtures from '../../shared/fixtures/expressions.json';
import { evaluate, evaluateSource, parse } from '../src/expressions';

describe('expression language parity with the backend', () => {
  it.each(fixtures.valid)('$source', ({ source, scope, value }) => {
    const result = evaluate(parse(source), scope as Record<string, number>);
    if (value === null) expect(Number.isFinite(result)).toBe(false);
    else expect(result).toBeCloseTo(value, 12);
  });
  it.each(fixtures.invalid)('rejects %j', source => {
    expect(() => parse(source)).toThrow();
    expect(Number.isNaN(evaluateSource(source, {}))).toBe(true);
  });
});
