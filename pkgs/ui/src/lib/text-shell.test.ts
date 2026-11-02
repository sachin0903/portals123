import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
	CELL_ADVISER,
	CELL_INSTRUMENT,
	CELL_NAME,
	clipName,
	CELL_SPINE,
	TRUNCATE_BLOCK,
	TRUNCATE_ROW,
	WRAP_NAME,
	WRAP_TOKEN
} from './text-shell';

/**
 * The text shell is only worth having if the containment it describes actually works.
 *
 * Every rule here failed silently at least once in this codebase, and that is the point: none of
 * them is a type error, and the page still renders — it just overflows. `truncate` was applied 157
 * times across this package and mostly did nothing, in three separate ways (no width bound, the
 * `min-w-0` on the container instead of the item, an inline box where `overflow` does not apply).
 * So the presets encode the fix and this pins the properties that make each one work.
 */

const read = (relative: string) =>
	readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8');

describe('text shell', () => {
	it('puts `min-w-0` on the item that truncates, not on its container', () => {
		// The failure this exists to make unwriteable: a flex item's automatic minimum size is its
		// min-content width, which for a nowrap string is the WHOLE string — so the item refuses to
		// shrink and either overflows or squashes its sibling. `flex-1` does not fix it: `flex: 1 1 0%`
		// sets the basis, not the minimum.
		expect(TRUNCATE_ROW).toContain('min-w-0');
		expect(TRUNCATE_ROW).toContain('truncate');
	});

	it('makes a bare span a BLOCK before asking it to clip', () => {
		// `overflow` does not apply to a non-replaced inline box, so `truncate` on a bare `<span>` in a
		// table cell is a no-op. (Inside a flex or grid container the item is blockified and it works,
		// which is why identical-looking markup behaved differently in different places.)
		expect(TRUNCATE_BLOCK).toContain('block');
		expect(TRUNCATE_BLOCK).toContain('truncate');
	});

	it('wraps a name ANYWHERE, and never with break-all', () => {
		// An email, a filename or a hyphenated surname has no break opportunity (`_`, `@` and `.` are
		// not), so it overflows a bounded box even where wrapping would be welcome. `wrap-anywhere` also
		// affects min-content sizing, so unlike `break-words` it actually lets a flex item shrink.
		// `break-all` is wrong for anything a human reads — it shreds the readable part of an email.
		expect(WRAP_NAME).toBe('wrap-anywhere');
		expect(WRAP_NAME).not.toContain('break-all');
	});

	it('breaks a TOKEN mid-word, because an id has no word to keep whole', () => {
		expect(WRAP_TOKEN).toBe('break-all');
	});

	it('gives the identity column the leftover width instead of a ceiling', () => {
		// `max-w-0` looks wrong and is not: under `table-layout: auto` it is the hint that lets the
		// cell shrink below its content, and `w-full` then claims whatever the fixed columns did not
		// use. Without the pair, `truncate` in that cell has nothing to clip against.
		expect(CELL_SPINE).toContain('w-full');
		expect(CELL_SPINE).toContain('max-w-0');
		// A ceiling would defeat the point — this column is the one that gives way.
		expect(CELL_SPINE).not.toMatch(/max-w-\[/);
	});

	it('pairs every ceiling with the truncate that needs it', () => {
		// A `max-w-` with no `truncate` does not clip, it OVERFLOWS the capped box — a distinct bug
		// shape that exists in this repo (`ReconView`'s `max-w-80` under `whitespace-nowrap`).
		for (const preset of [CELL_NAME, CELL_ADVISER, CELL_INSTRUMENT]) {
			expect(preset).toMatch(/max-w-\[\d+rem\]/);
			expect(preset).toContain('truncate');
		}
	});

	it('caps a queue name at thirty characters and keeps the rest for the tooltip', () => {
		expect(clipName('Aviva')).toEqual({ text: 'Aviva' });
		const long = 'Hargreaves Lansdown Asset Management Ltd';
		const clipped = clipName(long);
		expect(clipped.text).toHaveLength(30);
		expect(clipped.text?.endsWith('…')).toBe(true);
		expect(clipped.full).toBe(long);
	});

	it('gives an instrument name more room than a person’s, because the data says so', () => {
		// Measured over the committed Seccl extract: fund names reach 89 characters (p95 64) where an
		// adviser's longest is 19. These are CEILINGS, not haircuts — for most of them the cap never
		// fires on ordinary data and only catches the outlier that would blow out the row.
		const rem = (preset: string) => Number(/max-w-\[(\d+)rem\]/.exec(preset)?.[1]);
		expect(rem(CELL_INSTRUMENT)).toBeGreaterThan(rem(CELL_NAME));
		expect(rem(CELL_NAME)).toBeGreaterThan(rem(CELL_ADVISER));
	});

	it('is what the surfaces actually import', () => {
		// A shell nobody composes is a file, not a rule. Named individually rather than globbed so
		// that deleting a usage is a deliberate edit here too.
		const users = [
			'components/shell/page-header.svelte',
			'components/book/accounts/accounts-listing.svelte',
			'components/book/accounts/account-detail.svelte',
			'components/book/clients/clients-listing.svelte',
			'components/book/clients/client-record.svelte',
			'components/book/documents/document-detail.svelte'
		];
		for (const path of users) {
			expect(read(path), path).toMatch(/text-shell/);
		}
	});
});
