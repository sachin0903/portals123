/**
 * Both Activity components must render on the server.
 *
 * This is a SMOKE TEST and deliberately nothing more. SSR has broken twice here and both times it
 * took the page down rather than degrading — `bind:` to a name a snippet shadowed, and a
 * `Collapsible` bound to an unseeded key — and neither is visible to `svelte-check`, a mount test
 * or a source scan. What SSR can tell you is "does it throw", so that is all this asks.
 *
 * It does NOT assert column order, tab labels, which control the toolbar uses or what the timeline
 * says. Those were separate SSR tests and they earned nothing: the rules they covered are either
 * pure functions tested directly (`timelineFor`, `templateLabel`, `formatRawRequest`) or cosmetic
 * choices a reviewer sees in the diff. A test that only restates the markup fails on every
 * refactor and catches no defect.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import MessageStream from './message-stream.svelte';
import MessageDetail from './message-detail.svelte';
import type { CommsMessage } from './describe.js';

const message = (over: Partial<CommsMessage> = {}): CommsMessage => ({
	id: 1,
	uid: 'abc123abc123',
	status: 'sent',
	subject: "You've been invited to Secret Co Platform",
	to_address: 'ada@example.test',
	to_domain: 'example.test',
	recipient_kind: 'contact',
	recipient_contact_id: 7,
	recipient_name: null,
	template_code: 'invite',
	event_type: 'member.invited',
	rule_uid: null,
	category: 'auth',
	source: 'worker',
	error: null,
	provider_message_id: null,
	created_at: '2026-09-01T12:00:00.000Z',
	sent_at: '2026-09-01T12:00:00.000Z',
	delayed_at: null,
	delivered_at: null,
	bounced_at: null,
	complained_at: null,
	...over
});

describe('Activity renders on the server', () => {
	it('renders the listing, including the filters that read state inside a snippet', () => {
		// The regression: `FilterSelect` bound to `filters.status` inside a snippet NAMED `filters`
		// reads the snippet, not the state — `undefined[0]` on the server, a 500 on the page.
		const { body } = render(MessageStream, {
			props: { messages: [message()], hasMore: false }
		});
		expect(body).toContain('ada@example.test');
	});

	it('renders the detail, with and without a body to show', () => {
		const withBody = render(MessageDetail, {
			props: { message: message({ html: '<p>hi</p>', delivered_at: '2026-09-01T12:02:00.000Z' }) }
		});
		expect(withBody.body).toContain('ada@example.test');

		// The other half of the branch: a suppressed send has no body and no stamps, and the pane
		// has to say so rather than render an empty frame.
		const withoutBody = render(MessageDetail, {
			props: { message: message({ status: 'suppressed', sent_at: null }) }
		});
		expect(withoutBody.body).toContain('ada@example.test');
	});
});
