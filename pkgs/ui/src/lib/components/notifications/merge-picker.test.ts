import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { filterParams, groupParams, tokensIn, unknownTokens } from './merge-picker.js';
import type { CatalogueParam } from './rule-types.js';

const p = (key: string, label = key, example = 'x'): CatalogueParam => ({ key, label, example });

describe('tokensIn', () => {
	it('collects the dotted tokens across title + body, lowercased, deduped', () => {
		const used = tokensIn('Hi {client.name}', 'from {actor.name} on {event.when} — {client.name}');
		expect(used).toEqual(new Set(['client.name', 'actor.name', 'event.when']));
	});

	it('is case-insensitive and ignores non-tokens', () => {
		expect(tokensIn('{Client.Name} {not a token} { spaced }')).toEqual(new Set(['client.name']));
	});

	it('handles null/blank copy', () => {
		expect(tokensIn(null, undefined, '')).toEqual(new Set());
	});
});

describe('groupParams', () => {
	it('groups by namespace in the human order, with labels', () => {
		const groups = groupParams([
			p('firm.name'),
			p('client.name'),
			p('event.when'),
			p('client.email'),
			p('adviser.name')
		]);
		expect(groups.map((g) => g.namespace)).toEqual(['client', 'adviser', 'event', 'firm']);
		expect(groups[0].label).toBe('Client');
		expect(groups[0].params.map((x) => x.key)).toEqual(['client.name', 'client.email']); // order kept
		expect(groups.find((g) => g.namespace === 'event')?.label).toBe('Event');
	});

	it('trails an unknown namespace with a capitalised fallback label', () => {
		const groups = groupParams([p('firm.name'), p('widget.colour')]);
		expect(groups.map((g) => g.namespace)).toEqual(['firm', 'widget']);
		expect(groups[1].label).toBe('Widget');
	});
});

describe('filterParams', () => {
	const params = [p('client.name', 'Client name'), p('actor.email', 'Who did it (email)')];
	it('matches on label or key, case-insensitively', () => {
		expect(filterParams(params, 'email').map((x) => x.key)).toEqual(['actor.email']);
		expect(filterParams(params, 'CLIENT').map((x) => x.key)).toEqual(['client.name']);
	});
	it('returns everything for a blank query', () => {
		expect(filterParams(params, '   ')).toHaveLength(2);
	});
});

describe('unknownTokens', () => {
	const params = [p('client.name'), p('actor.name')];
	it('flags tokens the trigger does not provide, sorted', () => {
		const used = tokensIn('{client.name} {mystery.field} {another.one}');
		expect(unknownTokens(used, params)).toEqual(['another.one', 'mystery.field']);
	});
	it('is empty when every used token is known', () => {
		expect(unknownTokens(tokensIn('{client.name}'), params)).toEqual([]);
	});
});

/** THE CROSS-LANGUAGE PIN. This module's own comment claims the token shape is "the same shape
 *  app/notifications/render.py recognises", and nothing checked it. The two patterns are
 *  character-identical today; a divergence is silent in the worse direction — the picker would
 *  offer, or the linter accept, a token the renderer leaves as literal braces in a client's inbox. */
describe('parity with app/notifications/render.py', () => {
	const source = readFileSync(
		join(
			fileURLToPath(new URL('../../../../../../', import.meta.url)),
			'apps/api/app/notifications/render.py'
		),
		'utf8'
	);

	it('reads the Python, so the assertions below cannot pass vacuously', () => {
		expect(source).toContain('_TOKEN = re.compile(');
	});

	// Read from OUR source too, rather than exporting the regex purely for a test: the sibling
	// Reading our own source rather than exporting the regex purely for a test; the grammar is
	// internal to this module.
	const ours = readFileSync(join(import.meta.dirname, 'merge-picker.ts'), 'utf8');
	const oursPattern = (): string => {
		const m = ours.match(/^const TOKEN = \/(.+)\/([a-z]*);$/m);
		expect(m, 'the TOKEN declaration moved — this pin reads it by shape').toBeTruthy();
		return (m as RegExpMatchArray)[1];
	};

	it('recognises exactly the token shape the renderer does', () => {
		const python = source.match(/_TOKEN = re\.compile\(r"(.+?)",/)?.[1];
		expect(python).toBe(oursPattern());
		// And case-insensitively on both sides, so a mis-cased `{Client.Name}` is caught as a use
		// rather than missed — the renderer resolves it, so the picker must see it.
		expect(ours.match(/^const TOKEN = \/.+\/([a-z]*);$/m)?.[1]).toContain('i');
		expect(source).toContain('re.IGNORECASE');
	});

	it('agrees on what is and is not a placeholder', () => {
		const matches = (copy: string): boolean => new RegExp(oursPattern(), 'i').test(copy);
		expect(matches('{client.name}')).toBe(true);
		expect(matches('{Client.Name}')).toBe(true);
		expect(matches('{account.number_2}')).toBe(true);
		// A stray brace, a space inside, and a leading digit are all left untouched as literal text.
		expect(matches('{ client.name }')).toBe(false);
		expect(matches('{2fa}')).toBe(false);
		expect(matches('a { b')).toBe(false);
	});
});
