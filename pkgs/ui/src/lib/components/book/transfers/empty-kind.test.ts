/**
 * The empty message is chosen by whether the list is narrowed.
 *
 * The stage used to be a client-side strip that `dirty` could not see, so clicking an empty stage
 * said "No transfers yet" about a full book. That strip is gone: stage, route, basis and adviser
 * are toolbar filters, and in server mode they live on the query. `dirty` is therefore the state
 * of the list, and it is the right question for the empty message.
 */

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./transfers-workbench.svelte', import.meta.url), 'utf8');

describe('the transfers empty state', () => {
	it('asks whether the toolbar has narrowed the list', () => {
		expect(source).not.toContain('stageFilter');
		expect(source).toMatch(/empty=\{dirty \?/);
	});

	it('clears every toolbar filter, including the route', () => {
		const clear = /function clear\(\) \{([\s\S]*?)\n\t\}/.exec(source)?.[1] ?? '';
		expect(clear, 'clear() not found').not.toBe('');
		expect(clear).toContain('routeFilter = ALL');
		expect(clear).toContain('filters: {}');
		const matches = /\{#snippet noMatches\(\)\}([\s\S]*?)\{\/snippet\}/.exec(source)?.[1] ?? '';
		expect(matches, 'no narrowed-state snippet').not.toBe('');
		expect(matches).toContain('action={clearFilters}');
	});

	it('still states the two kinds differently', () => {
		const empty = /\{#snippet noTransfers\(\)\}([\s\S]*?)\{\/snippet\}/.exec(source)?.[1] ?? '';
		expect(empty).toContain('No transfers yet');
		expect(empty).toContain('Transfer in');
		expect(empty).not.toContain('match your filters');
	});
});
