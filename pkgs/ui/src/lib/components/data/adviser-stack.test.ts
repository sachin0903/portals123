import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import AdviserStack, { shortName } from './adviser-stack.svelte';

// `names` is required by the component, so the helper states it and callers may override.
const html = (props: Record<string, unknown>) =>
	render(AdviserStack, { props: { names: [], ...props } as never }).body;

describe('AdviserStack (SSR)', () => {
	it('shows a dash rather than an empty cell when nobody is assigned', () => {
		expect(html({ names: [] })).toContain('—');
	});

	it('drops nulls and blanks the API can send', () => {
		// `advisers` arrives as a text[] from an array_agg, so a client with no adviser can be `[null]`
		// rather than `[]` — which would otherwise render an avatar for the name "".
		const body = html({ names: [null, '  ', undefined] });
		expect(body).toContain('—');
		expect(body).not.toContain('rounded-full');
	});

	it('displays the first adviser and a count, and keeps the full list for the tooltip', () => {
		// Joining every name set the column's width by how many advisers the busiest client has —
		// "Craig Currie, Sachin Sharma" is twice the string of "Craig C. +1" for a fact the second
		// states just as well, and in a book listing that difference was enough to push the table into a
		// horizontal scrollbar. The names are not lost: the faces are one per adviser and the tooltip
		// carries the list in full, which is why BOTH strings have to reach the client.
		const body = html({ names: ['Zoe Lead', 'Amelia Bright', 'Mark Alford', 'Jo Finch'], max: 3 });
		expect(body).toContain('Zoe L. +3');
		for (const name of ['Zoe Lead', 'Amelia Bright', 'Mark Alford', 'Jo Finch']) {
			expect(body, name).toContain(name); // present as the tooltip's `full`
		}
	});

	it('shortens a name to the first name and the last initial', () => {
		// A full name in a 12% column truncated to "Gio ..." on almost every row, which says less
		// than "Gio M." does in the same room. The face carries the initials and the tooltip the
		// whole name, so the cell can afford to be brief.
		expect(shortName('Gio Matonti')).toBe('Gio M.');
		expect(shortName('  Mary Anne  de la Cruz ')).toBe('Mary C.');
		expect(shortName('Zoë lead')).toBe('Zoë L.');
		// One word has no surname to shorten — an email or a mononym is left as it is.
		expect(shortName('Cher')).toBe('Cher');
		expect(shortName('m.cole@example.com')).toBe('m.cole@example.com');
		expect(shortName('')).toBe('');
	});

	it('gives a single adviser the short name, with the full name for the tooltip and a reader', () => {
		// The short form is a summary too, so the whole name must still reach the client: the
		// tooltip's `full`, and the `sr-only` span the avatars' `aria-hidden` makes necessary.
		const body = html({ names: ['Gio Matonti'] });
		expect(body).toContain('Gio M.');
		expect(body).toContain('Gio Matonti');
		expect(body).toContain('sr-only');
	});

	it('shows nothing extra for a name the short form cannot shorten', () => {
		// A mononym IS its own display: there is no fuller form for a tooltip to add.
		const body = html({ names: ['Cher'] });
		expect(body).toContain('Cher');
		expect(body).not.toContain('sr-only');
	});

	it('wears the badge-height face by default, so the row keeps its height', () => {
		// `xs` is `size-5` — the height of a Badge, so the face sits level with the Accounts pills in
		// the next column. The record's team panel, with room, passes `sm` explicitly.
		expect(html({ names: ['Gio Matonti'] })).toContain('size-5');
		expect(html({ names: ['Gio Matonti'], size: 'sm' })).toContain('size-7');
	});

	it('turns the advisers past `max` into a count', () => {
		const body = html({ names: ['A One', 'B Two', 'C Three', 'D Four', 'E Five'], max: 3 });
		expect(body).toContain('+2');
	});

	it('shows no count for a single adviser', () => {
		// One name is not a summary of anything, so it is shown plainly with nothing appended.
		const body = html({ names: ['A One'] });
		expect(body).toContain('A One');
		expect(body).not.toContain('+0');
		expect(body).not.toContain('+1');
	});

	it('counts the advisers, not the hidden faces', () => {
		// Two separate counts live in this component and they must not be confused: the avatar strip's
		// `+N` is how many FACES are hidden past `max`, while the text's `+N` is how many advisers are
		// not named. With four advisers and room for three faces they differ — 1 and 3 — and the text's
		// is the one about people.
		const body = html({ names: ['A One', 'B Two', 'C Three', 'D Four'], max: 3 });
		expect(body).toContain('A O. +3');
		expect(body).toContain('+1'); // the face chip
	});

	it('lets the names give way, not the faces', () => {
		// The failure this is built against: without `shrink-0` on the strip and `min-w-0` on the text,
		// a long adviser list squashes the avatars instead of clipping the names.
		const body = html({ names: ['Zoe Lead', 'Amelia Bright'] });
		expect(body).toContain('shrink-0');
		expect(body).toContain('min-w-0');
	});
});
