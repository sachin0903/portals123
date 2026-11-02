import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./model-assumptions.svelte', import.meta.url), 'utf8');

/**
 * FB-20261006-03: "<model> is sent at 6% a year" read as a postage rate. The line now says what
 * the model is — a target return and a charge — in the words an adviser would use to a client.
 */
describe('model assumptions line', () => {
	it('states the target return and the annual model charge, never "is sent at"', () => {
		expect(source).toContain('targets a return of {rate} a year,');
		expect(source).toContain('with a {charge} annual model charge.');
		expect(source).toContain('with no model charge recorded.');
		expect(source).not.toMatch(/\bis sent at \{rate\}/);
	});
});
