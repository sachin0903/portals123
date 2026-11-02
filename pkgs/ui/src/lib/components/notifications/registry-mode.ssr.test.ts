/**
 * The registry's rules about WHAT IT SHOWS — the ones a reader would be misled by if they broke.
 *
 * Deliberately short. This file used to carry eighteen SSR assertions, most of them restating
 * markup: column order, which filter controls exist, that a category label renders instead of a
 * raw resource name. Those fail on every refactor and catch nothing — the rules behind them are
 * either pure functions tested directly (`resourceCategory`, `triggersFrom`, `configuredState`)
 * or choices visible in a diff. What is left is the handful where a silent regression would leave
 * a firm with a wrong picture of what it notifies.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import NotificationRules from './notification-rules.svelte';
import type { CatalogueTrigger, NotificationRule } from './rule-types.js';

const audience = (over: Partial<CatalogueTrigger['audiences'][number]> = {}) => ({
	key: 'client',
	label: 'The client',
	builtin: true,
	default_title: 'Your report is ready',
	default_body: 'A new document is waiting.',
	default_link: null,
	default_in_app: true,
	default_email: true,
	default_email_code: 'document_sent',
	...over
});

const trigger = (over: Partial<CatalogueTrigger> = {}): CatalogueTrigger => ({
	key: 'document.shared',
	label: 'A document is shared with a client',
	description: 'Fires when a document is sent.',
	category: 'documents',
	event_types: ['document.sent'],
	subject_kind: 'contact',
	link_targets: [],
	params: [],
	audiences: [audience()],
	...over
});

const registryEvent = (over: Record<string, unknown> = {}) => ({
	event_type: 'document.sent',
	resource: 'document',
	state: 'assignable' as const,
	unavailable_reason: null,
	subject_kinds: ['contact'],
	trigger: trigger(),
	rules: [] as NotificationRule[],
	automation_count: 0,
	email_count_30d: 0,
	...over
});

const stored: NotificationRule = {
	uid: 'nrule_1',
	trigger_key: 'document.shared',
	audience: 'client',
	role_slug: null,
	label: 'Document sent',
	title: 'Your report is ready',
	body: null,
	link: null,
	priority: 'normal',
	default_in_app: true,
	default_email: true,
	mandatory: false,
	firm_editable: true,
	authored_by: 'graphene',
	enabled: true,
	email_code: 'document_sent'
};

describe('what the registry shows', () => {
	it('shows a shipped default as a row, which is the whole point of it', () => {
		// A built-in is not a stored rule, so the old builder listed nothing — a firm receiving the
		// `document.shared` client notice saw "nothing configured" and had no way to change it.
		const { body } = render(NotificationRules, {
			props: { events: [registryEvent()], rules: [], groupBy: 'event' }
		});
		expect(body).toContain('The client');
		expect(body).toContain('default');
	});

	it('hides the events that can reach nobody, and says how many', () => {
		// 172 of 207 resolve no recipient. Listing them all put a wall of "—" between a reader and
		// the 35 they can act on; hiding them silently would make the list claim to be everything.
		// `declarable` is hidden with `actor_only`: it records a subject but no source is declared
		// for it, so it reaches nobody either — the difference is ours, not the reader's.
		const unreachable = (over: Record<string, unknown>) =>
			registryEvent({ trigger: null, subject_kinds: [], rules: [], ...over });
		const { body } = render(NotificationRules, {
			props: {
				events: [
					registryEvent(),
					unreachable({
						event_type: 'authentication.failed',
						resource: 'authentication',
						state: 'actor_only'
					}),
					unreachable({ event_type: 'account.viewed', resource: 'account', state: 'declarable' })
				],
				rules: [],
				groupBy: 'event'
			}
		});
		expect(body).toContain('document.sent');
		expect(body).not.toContain('authentication.failed');
		expect(body).not.toContain('account.viewed');
		// The count of what is hidden — without it the list quietly claims to be the whole registry.
		expect(body).toContain('2');
	});

	it('does not offer a category whose every event is hidden', () => {
		// Otherwise picking it lands on an empty table: the option is drawn from the rows the table
		// would show WITHOUT the category filter, not from every row that exists.
		const { body } = render(NotificationRules, {
			props: {
				events: [
					registryEvent(),
					registryEvent({
						event_type: 'authentication.failed',
						resource: 'authentication',
						state: 'actor_only',
						trigger: null,
						subject_kinds: []
					})
				],
				rules: [],
				groupBy: 'event'
			}
		});
		expect(body).not.toContain('Security');
	});

	it('still renders the audience grouping the admin surface mounts', () => {
		// One smoke test for the other grouping: it takes `catalogue` rather than `events`, and a
		// registry-shaped assumption in shared code would 500 a page nobody was looking at.
		const { body } = render(NotificationRules, {
			props: { catalogue: [trigger()], rules: [stored] }
		});
		expect(body).toContain('Your report is ready');
	});
});
