import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import EventStream from './event-stream.svelte';
import EventDetail from './event-detail.svelte';
import type { ActionEvent } from './describe.js';
import { EMPTY_ACTION_LOG_FILTERS } from './event-stream.svelte';

const proxied = (over: Partial<ActionEvent> = {}): ActionEvent => ({
	id: 7,
	event_type: 'client.updated',
	created_at: '2026-08-03T09:15:00Z',
	method: 'PATCH',
	route: '/firm/contacts/{contact_id}',
	status: 200,
	path_params: { contact_id: 96 },
	metadata: {},
	actor_id: 'u-9',
	actor_sub: 'wos_ops_alice',
	actor_email: 'alice@graphene.test',
	actor_name: 'Alice Ops',
	tenant_id: 1,
	tenant_uid: 'graphene',
	tenant_name: 'Graphene Operations',
	target_tenant_id: 9,
	target_tenant_uid: 'ten_firm',
	target_tenant_name: 'Feed Firm',
	...over
});

// `events` is the only required prop now — the filters are client-side, so there is no fetcher to
// inject and nothing to announce.
const base = {};

// The raw JSON ("Details") shows the full event — on a proxied row it names the ops tenant. The
// firm HUMANISED view still abstracts that to "Graphene support", so the attribution rail scopes to
// the part before the Details section.
const humanised = (body: string) => body.split('Details')[0];

describe('EventStream — firm posture', () => {
	it('attributes a proxied row with a "Graphene support" chip, and does not tint the row', () => {
		const body = render(EventStream, {
			props: { ...base, attribution: 'firm' as const, events: [proxied()] }
		}).body;
		expect(body).toContain('Graphene support');
		// The chip does NOT repeat the operator: the Who column names them, with their avatar, one
		// column to the left. Saying it twice on one line costs the width the action itself needs.
		expect(body).not.toContain('Graphene support · Alice Ops');
		// …and the operator IS still named, in the column that is for exactly that.
		expect(body).toContain('Alice Ops');
		// NO row tint. The chip carries the distinction; tinting the whole row amber reads as a warning
		// about the event rather than a statement about who came in, and on a firm with a week-long
		// support ticket it stains most of the table, at which point it distinguishes nothing.
		expect(body).not.toContain('bg-warning-subtle/40');
		// The reader IS the target, and the acting-tenant chip would name Graphene's internal tenant
		// on every proxied row — neither belongs in the firm's own HUMANISED view (the raw Details
		// JSON names it, deliberately — that is the transparency the trail exists for).
		expect(humanised(body)).not.toContain('Acting on Feed Firm');
		expect(humanised(body)).not.toContain('Graphene Operations');
	});

	it('carries the Graphene (acting tenant) icon on the support badge when branding sets one', () => {
		const withIcon = render(EventStream, {
			props: {
				...base,
				attribution: 'firm' as const,
				events: [proxied({ tenant_icon_url: 'https://cdn.test/graphene-mark.svg' })]
			}
		}).body;
		expect(withIcon).toContain('src="https://cdn.test/graphene-mark.svg"');
		expect(withIcon).toContain('Graphene support');

		// No icon set ⇒ the badge stays text-only (the pre-icons posture, byte-compatible).
		const without = render(EventStream, {
			props: { ...base, attribution: 'firm' as const, events: [proxied()] }
		}).body;
		expect(without).not.toContain('<img');
	});

	it('keeps the operations attribution as the default (backwards-compatible)', () => {
		const body = render(EventStream, { props: { ...base, events: [proxied()] } }).body;
		// The support chip is the same two words on both surfaces — it answers WHO, and who came in is
		// the same fact whichever side is reading. What differs is the firm chip: operations also names
		// the firm the row is ABOUT (the target), which a firm reading its own trail does not need.
		expect(body).toContain('Graphene support');
		expect(body).toContain('Feed Firm');
		// …and never the operator's name in the chip: the Who column names them one column left.
		expect(body).not.toContain('Graphene support ·');
	});

	it('hides the firm dropdown when showTenantFilter is false, keeping the rest of the toolbar', () => {
		const hidden = render(EventStream, {
			props: { ...base, showTenantFilter: false, events: [] }
		}).body;
		expect(hidden).not.toContain('All firms');
		expect(hidden).toContain('aria-label="Show filters"');

		const chosen = render(EventStream, {
			props: { ...base, events: [], filters: { ...EMPTY_ACTION_LOG_FILTERS, tenant: ['ten_a'] } }
		}).body;
		expect(chosen).toContain('aria-label="Hide filters (1 applied)"');
	});

	// `links` is read by the DETAIL, which only exists once a reader opens a row — the stream no
	// longer unfolds one for them. So these render the panel the way the stream's expanded cell
	// does; that the cell holds this component is pinned in event-stream.expand.dom.test.ts.
	const panelOf = (event: ActionEvent, links?: (key: string, value: unknown) => string | null) =>
		render(EventDetail, { props: { event, variant: 'inline', links } }).body;

	it('links a target id chip via the links prop, and leaves unmapped keys as plain text', () => {
		const body = panelOf(
			proxied({ path_params: { contact_id: 96, step_key: 'kyc' } }),
			(key, value) => (key === 'contact_id' ? `/clients/${value}` : null)
		);
		expect(body).toContain('href="/clients/96"');
		expect(body).toContain('step_key');
		expect(body).not.toContain('href="/clients/kyc"');
	});

	it('renders no links at all when the prop is absent', () => {
		const body = panelOf(proxied());
		expect(body).toContain('contact_id'); // the param is stated…
		expect(body).not.toContain('href="/clients/96"'); // …but nothing became a link
	});
});
