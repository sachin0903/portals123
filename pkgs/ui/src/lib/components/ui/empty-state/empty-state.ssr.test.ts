/**
 * THE EMPTY STATE HAS TO SAY WHICH KIND OF NOTHING IT IS.
 *
 * Four states render alike and mean opposite things: you have none yet, none match your filter, you
 * may not read them, and we could not read them. The last two are statements about US; the first
 * two are statements about the READER'S BOOK. Dressing a failed read as "you have nothing yet" is
 * therefore a false statement about someone's own money, and this estate has paid for that shape
 * repeatedly elsewhere — a merge reject that "reads identically to 'this tenant has no data here'",
 * a picker that raises rather than returning `[]` "because an empty dropdown that means
 * 'unreadable' looks exactly like one that means 'this firm has no staff there'".
 *
 * Words alone do not carry it, because a reader matches the SHAPE before reading the sentence. So
 * the two failure kinds wear `LoadErrorBanner`'s own tokens, and this file pins that they are
 * visibly distinct from the neutral one rather than merely differently worded.
 */

import { createRawSnippet, type ComponentProps } from 'svelte';
import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import EmptyState from './empty-state.svelte';

/** Comments stripped: Svelte keeps them in SSR output, and two of the assertions below are about
 *  class strings that also appear in this component's own prose. */
const body = (props: ComponentProps<typeof EmptyState>) =>
	render(EmptyState, { props }).body.replace(/<!--[\s\S]*?-->/g, '');

describe('the neutral kinds', () => {
	it('is a dashed card carrying the title', () => {
		const html = body({ title: 'No clients yet' });
		expect(html).toContain('No clients yet');
		expect(html).toContain('border-dashed');
		expect(html).not.toContain('bg-warning-subtle');
	});

	it('renders a description when there is one', () => {
		expect(body({ title: 'No clients yet', description: 'Add one to begin.' })).toContain(
			'Add one to begin.'
		);
	});

	it('is compact inside a card', () => {
		expect(body({ title: 'No fees', compact: true })).toContain('px-4 py-6');
		expect(body({ title: 'No fees' })).toContain('p-10');
	});
});

describe('the two kinds that are about US, not the reader', () => {
	// `tone="warning"` is for a read that FAILED and one the caller may not make. Both must be
	// recognisable before the sentence is read, which means chrome, not wording.
	it('wears the load-failure tokens, so it cannot be mistaken for "you have nothing"', () => {
		const html = body({
			title: "Accounts couldn't be loaded",
			description: 'This is an error, not an empty book.',
			tone: 'warning'
		});
		expect(html).toContain('border-warning-line');
		expect(html).toContain('bg-warning-subtle');
		expect(html).toContain('text-warning-strong');
		// And NOT the neutral treatment, or the two kinds would be one again.
		expect(html).not.toContain('border-dashed');
	});

	it('shares those tokens with LoadErrorBanner, the page-level statement of the same fact', () => {
		// Not a style preference: a failed slice and a failed page must look like the same event.
		// The banner is one statement about the whole screen and carries the retry; this is stated
		// where the missing thing would have been. Two scopes, one appearance.
		const html = body({ title: "Couldn't load", tone: 'warning' });
		for (const token of ['border-warning-line', 'bg-warning-subtle', 'text-warning-strong']) {
			expect(html, `LoadErrorBanner uses ${token}`).toContain(token);
		}
	});
});

describe('chromeless', () => {
	it('keeps the words and drops the card, for a look built on having no chrome', () => {
		const html = body({ title: 'No holdings yet', chromeless: true });
		expect(html).toContain('No holdings yet');
		expect(html).not.toContain('border-dashed');
		expect(html).not.toContain('rounded-xl');
	});

	it('still marks a failure as a failure', () => {
		// The client portal's chrome-less looks can degrade too, and losing the border must not lose
		// the distinction — that is the whole reason the six hand-rolled copies were consolidated.
		const html = body({ title: "We can't show your holdings", tone: 'warning', chromeless: true });
		expect(html).toContain('text-warning-strong');
		expect(html).not.toContain('rounded-xl');
	});
});

describe('the one action that fixes it', () => {
	it('renders the action, which is half of what the rule asks for', () => {
		// "Says what is missing and the one action that fixes it" — 70 of the estate's 93 empty
		// states shipped with only the first half, and Carbon names that shape too: "don't lead the
		// user into a dead end".
		const action = createRawSnippet(() => ({
			render: () => '<button type="button">Add a client</button>'
		}));
		const html = body({ title: 'No clients yet', action });
		expect(html).toContain('Add a client');
		expect(html).toContain('justify-center');
	});

	it('has no action slot rendered when none is given', () => {
		expect(body({ title: 'No clients yet' })).not.toContain('justify-center');
	});
});
