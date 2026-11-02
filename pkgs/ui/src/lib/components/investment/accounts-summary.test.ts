/**
 * The accounts summary is ONE card holding a list of metrics — not a card of cards.
 *
 * THE DEFECT. It rendered each product type as a `StatTile`, which is the CARDED tile ("the carded
 * KPI tile" in its own docstring). So a block that is one idea — what this client holds, by wrapper
 * — arrived as a bordered panel containing five more bordered panels, reading as five separate
 * blocks stacked inside a box.
 *
 * A source-shape test, like `chrome.test.ts` and `switch-columns.test.ts`: the nesting is a
 * composition decision with no behaviour to drive, and it came back the moment someone reached for
 * the tile because the tile is the obvious component to reach for. What is asserted is the rule
 * ("this file does not use the carded tile"), not the markup around it.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const src = readFileSync(join(import.meta.dirname, 'accounts-summary.svelte'), 'utf8');
/** Markup only — the note above the props block names the tile in prose, deliberately. */
const markup = src.slice(src.lastIndexOf('</script>'));

describe('the accounts summary composition', () => {
	it('does not put a card inside its card', () => {
		expect(markup).not.toContain('StatTile');
		expect(src).not.toContain("from '../data/stat-tile.svelte'");
	});

	it('renders the figures with the UNCARDED primitive', () => {
		expect(src).toContain("from '../data/stat.svelte'");
		expect(markup).toContain('<Stat');
	});

	it('is still a card itself', () => {
		// Bare figures on the page background read as a rendering error beside carded neighbours —
		// the fix is one card, not none.
		expect(markup).toContain('<Card.Root');
		expect(markup).toContain('<Card.Content>');
	});

	it('separates the figures with a hairline rather than a border each', () => {
		// One line between metrics is what makes a list read as a list; a border around each is
		// what made it read as five cards.
		expect(markup).toContain('divide-y');
	});

	it('keeps the figures in a description list', () => {
		// A label/value set is a `dl`, which is also what makes each pair announce together.
		expect(markup).toContain('<dl');
	});

	it('still lays out against the CONTAINER, not the viewport', () => {
		// A summary in a half-width block gets two columns, not three cramped ones — the reason
		// this block was container-queried in the first place, and easy to lose in a rewrite.
		expect(markup).toContain('@container');
		expect(markup).toContain('@lg:grid-cols-2');
	});
});
