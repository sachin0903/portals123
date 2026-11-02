/**
 * The read side of the September 2026 event-content review.
 *
 * The finding, measured across the live route table: 54 event types carried an id in their path
 * that nothing could resolve, so the row showed a bare uid where a name belongs; 32 more named the
 * record and said nothing about the change. "Set a transfer's status" was the whole sentence, for
 * every transfer, to every status.
 *
 * Two things answer it here — the api half is pinned in `apps/api/tests/test_event_content.py`:
 *   * new entity kinds, so a single-verb sentence can NAME the record it touched;
 *   * `metadata.changed` — the field NAMES an update carried, never their values — woven into the
 *     sentence and stated in full in the expanded detail.
 */
import { describe, expect, it } from 'vitest';
import {
	changedFields,
	describeActionEvent,
	entityKindLabel,
	fieldWord,
	type ActionEntity,
	type ActionEvent
} from './describe.js';
import {
	bodyFields,
	changeRows,
	detailRows,
	fieldLabel,
	NOT_SET,
	recordedChanges
} from './event-detail.js';

const named = (kind: string, label: string, id = '1'): ActionEntity => ({
	kind,
	id,
	uid: id,
	label
});

describe('the changed-fields clause', () => {
	it('names what an otherwise silent update actually touched', () => {
		// The row this replaces read "Updated a client" whether an email or a date of birth moved.
		const entities = [named('contact', 'Margaret Cole', 'c1')];
		expect(
			describeActionEvent('client.updated', { contact_uid: 'c1' }, entities, {
				changed: ['email', 'dob']
			})
		).toBe('Updated Margaret Cole: email, dob');
	});

	it('keeps the order the API captured, which is sorted', () => {
		expect(
			describeActionEvent('client.updated', {}, [], { changed: ['dob', 'email', 'phone'] })
		).toBe('Updated a client: dob, email, phone');
	});

	it('stops listing at three and counts the rest', () => {
		expect(
			describeActionEvent('client.updated', {}, [], {
				changed: ['address', 'dob', 'email', 'phone', 'nationality']
			})
		).toBe('Updated a client: address, dob, email and 2 more');
	});

	it('leaves a row with no capture exactly as it read before', () => {
		expect(describeActionEvent('client.updated', {}, [], {})).toBe('Updated a client');
		expect(describeActionEvent('client.updated', {}, [], null)).toBe('Updated a client');
	});

	it('ignores a mis-shaped capture rather than rendering junk', () => {
		const metadata = { changed: 'email' } as unknown as ActionEvent['metadata'];
		expect(describeActionEvent('client.updated', {}, [], metadata)).toBe('Updated a client');
		expect(changedFields(metadata)).toEqual([]);
	});

	it('applies to a curated sentence too, so no path is left out', () => {
		expect(
			describeActionEvent('firm.provider_updated', { tenant_uid: 't1', provider: 'seccl' }, [], {
				subject: { enabled: true },
				changed: ['config', 'enabled']
			})
		).toBe('Updated a firm’s provider: config, enabled');
	});
});

describe('fieldWord', () => {
	it('reads as part of a sentence, not as the start of one', () => {
		expect(fieldWord('employer_name')).toBe('employer name');
		expect(fieldWord('relevant_uk_earnings')).toBe('relevant uk earnings');
	});

	it('drops the plumbing suffix a reader does not need', () => {
		expect(fieldWord('model_id')).toBe('model');
		expect(fieldWord('category_uid')).toBe('category');
		expect(fieldWord('role_slug')).toBe('role');
	});

	it('never lower-cases an acronym', () => {
		expect(fieldWord('sql')).toBe('SQL');
		expect(fieldWord('ai_enabled')).toBe('AI enabled');
	});
});

describe('the sentences the review added', () => {
	const cases: Array<[string, Parameters<typeof describeActionEvent>, string]> = [
		[
			'a role names itself and the portal it is for',
			['role.created', {}, [], { subject: { slug: 'compliance_officer', portal: 'platform' } }],
			'Created the Compliance officer role for the platform portal'
		],
		[
			'granting a portal says which portal and in which role',
			[
				'member.portal_granted',
				{ user_id: 'u1', portal: 'platform' },
				[named('user', 'm.cole@example.com', 'u1')],
				{ subject: { role_slug: 'adviser' } }
			],
			'Gave m.cole@example.com the platform portal as Adviser'
		],
		[
			'revoking one says the same thing in reverse',
			[
				'member.portal_revoked',
				{ user_id: 'u1', portal: 'client' },
				[named('user', 'm.cole@example.com', 'u1')],
				{}
			],
			'Took the client portal from m.cole@example.com'
		],
		[
			'a status change states both ends',
			[
				'contribution.status_updated',
				{ uid: 'k1' },
				[named('contribution', 'Margaret Cole - GBP 500.00', 'k1')],
				{ subject: { status: 'ml_recorded' }, previous: { status: 'matched' } }
			],
			'Moved Margaret Cole - GBP 500.00 from Matched to Ml recorded'
		],
		[
			'and reads correctly on an older row with no before-value',
			[
				'transfer.status_updated',
				{ uid: 't1' },
				[named('transfer', 'Margaret Cole - Aviva', 't1')],
				{ subject: { status: 'funds_sent' } }
			],
			'Set Margaret Cole - Aviva to Funds sent'
		],
		[
			'a reclassification says what it moved away from',
			[
				'contribution.categorised',
				{ uid: 'k1' },
				[named('contribution', 'Margaret Cole - GBP 500.00', 'k1')],
				{ subject: { category: 'employer' }, previous: { category: 'personal' } }
			],
			'Reclassified Margaret Cole - GBP 500.00: Personal → Employer'
		],
		[
			'a cancellation carries the reason, which is the act',
			[
				'contribution.cancelled',
				{ uid: 'k1' },
				[named('contribution', 'Margaret Cole - GBP 500.00', 'k1')],
				{ subject: { reason: 'Duplicate of the March payment' } }
			],
			'Cancelled Margaret Cole - GBP 500.00 — Duplicate of the March payment'
		],
		[
			'monitoring says who it was turned on for',
			[
				'aml.monitoring_updated',
				{ search_uid: 's1' },
				[named('aml_search', 'Margaret Cole', 's1')],
				{ subject: { enabled: true } }
			],
			'Turned monitoring on for Margaret Cole'
		],
		[
			'and off is a different sentence, not a missing one',
			[
				'aml.monitoring_updated',
				{ search_uid: 's1' },
				[named('aml_search', 'Margaret Cole', 's1')],
				{ subject: { enabled: false } }
			],
			'Turned monitoring off for Margaret Cole'
		],
		[
			'a data reset names the firm and how far it went',
			[
				'firm.data_reset_requested',
				{ tenant_uid: 'ten_1' },
				[named('tenant', 'Clifton Wealth', 'ten_1')],
				{ subject: { kind: 'demo_reset' } }
			],
			'Requested a demo reset data reset for Clifton Wealth'
		],
		[
			'a warehouse query records the query',
			[
				'platform.queried',
				{ provider: 'sei' },
				[],
				{ subject: { sql: 'select  *\n from platform.accounts' } }
			],
			'Ran a warehouse query on SEI: select * from platform.accounts'
		],
		[
			'an export names what left the building',
			['export.pdf_rendered', {}, [], { subject: { title: 'Clients' } }],
			'Exported Clients as PDF'
		],
		[
			'a pipeline act names the custodian it ran against',
			['platform.sync_started', { provider: 'sei' }, [], {}],
			'Started a sync on SEI'
		],
		[
			'and so does a rebuild',
			['platform.rebuilt', { provider: 'morgan-lloyd' }, [], {}],
			'Rebuilt the warehouse for Morgan Lloyd'
		],
		[
			'a batch generate says which template and how many clients',
			[
				'document.batch_generated',
				{},
				[named('template', 'Annual review', 't1')],
				{ subject: { template_uid: 't1', contact_ids: ['c1', 'c2', 'c3'] } }
			],
			'Generated Annual review for 3 clients'
		],
		[
			'and counts one client in the singular',
			[
				'document.batch_generated',
				{},
				[named('template', 'Annual review', 't1')],
				{ subject: { template_uid: 't1', contact_ids: ['c1'] } }
			],
			'Generated Annual review for 1 client'
		],
		[
			'a client portal invite names the client and the role',
			[
				'client.portal_invited',
				{ contact_uid: 'c1' },
				[named('contact', 'Margaret Cole', 'c1')],
				{ subject: { role_slugs: ['portal_user'] } }
			],
			'Invited Margaret Cole to the client portal as Portal user'
		],
		[
			'a beneficiary nomination states the share',
			[
				'account.beneficiary_added',
				{ account_uid: 'a1' },
				[named('account', 'SIPP 1234', 'a1')],
				{ subject: { relationship: 'spouse', proportion: '50' } }
			],
			'Nominated Spouse for 50% on SIPP 1234'
		]
	];

	it.each(cases)('%s', (_name, args, expected) => {
		expect(describeActionEvent(...args)).toBe(expected);
	});

	it('falls back to the plain sentence when the material is missing', () => {
		// Every builder returns null rather than half a sentence, so enrichment can only improve on
		// the generic reading — the rule the whole ENRICHED map is written to.
		expect(describeActionEvent('role.created', {}, [], {})).toBe('Created a role');
		expect(describeActionEvent('contribution.status_updated', { uid: 'k1' }, [], {})).toBe(
			'Updated contribution status'
		);
		expect(describeActionEvent('platform.queried', { provider: 'sei' }, [], {})).toBe(
			'Ran a warehouse query'
		);
	});
});

describe('the records the review taught the feed to name', () => {
	// `namedSubject` names the SUBJECT instead of describing its type, but only when the resource
	// has a resolvable kind. These are the kinds that gained one.
	const cases: Array<[string, string, string, string]> = [
		['automation.deleted', 'automation', 'Welcome new clients', 'Deleted Welcome new clients'],
		['content.published', 'content', 'Autumn budget', 'Published Autumn budget'],
		['dataset.deleted', 'dataset', 'Fund list', 'Deleted Fund list'],
		['file.deleted', 'file', 'passport.pdf', 'Deleted passport.pdf'],
		['journey.started', 'journey', 'Onboarding', 'Started Onboarding'],
		['transfer.created', 'transfer', 'Margaret Cole - Aviva', 'Created Margaret Cole - Aviva'],
		// The row that sent us looking a second time: it read "updated a notification rule" over a
		// bare `uid nrule_8f0c26e8…`, so a firm could not see WHICH notice it had changed.
		['notification_rule.deleted', 'notification_rule', 'Document ready', 'Deleted Document ready']
	];

	it.each(cases)('%s names the record', (eventType, kind, label, expected) => {
		expect(describeActionEvent(eventType, { uid: 'x1' }, [named(kind, label, 'x1')], {})).toBe(
			expected
		);
	});

	it('gives every new kind a readable chip label', () => {
		expect(entityKindLabel('document_request')).toBe('Document request');
		expect(entityKindLabel('message_group')).toBe('Group chat');
		expect(entityKindLabel('aml_hit')).toBe('Screening hit');
		expect(entityKindLabel('catalogue_provider')).toBe('Provider');
	});
});

describe('the body states only what nothing else does', () => {
	const withBody = (metadata: ActionEvent['metadata']): ActionEvent => ({
		id: 1,
		event_type: 'document.generated',
		created_at: '2026-09-23T09:15:00Z',
		method: 'POST',
		route: '/documents/generate',
		status: 200,
		path_params: {},
		metadata,
		entities: [{ kind: 'contact', id: '1', uid: 'c1', label: 'Theo Caldwell' }],
		actor_id: 'u-1',
		actor_sub: 'user_1',
		actor_email: null,
		actor_name: 'Alice Feed',
		tenant_id: 1,
		tenant_uid: 'ten_1',
		tenant_name: 'Clifton Wealth',
		target_tenant_id: null,
		target_tenant_uid: null,
		target_tenant_name: null
	});

	it('does not repeat a field the captured subject already shows', () => {
		// A body is the whole request, and most of a request is already on the row by a better
		// route. `document.generated` showed "Idempotency key" and "Allow incomplete" twice — once
		// as a change row from the subject, once from the body.
		const rows = bodyFields(
			withBody({
				correlation_id: 'c',
				subject: { idempotency_key: 'abc', allow_incomplete: true },
				body: { idempotency_key: 'abc', allow_incomplete: true, name: 'Annual review' }
			})
		);
		expect(rows.map(([label]) => label)).toEqual(['Name']);
	});

	it('does not print a raw id the row has already resolved to a name', () => {
		// The same event showed `client_contact_id` as a bare `1` beside the client chip it had
		// just named.
		const rows = bodyFields(
			withBody({ correlation_id: 'c', body: { client_contact_id: 1, name: 'Annual review' } })
		);
		expect(rows.map(([label]) => label)).toEqual(['Name']);
	});

	it('labels a field without stuttering', () => {
		// `client_contact_id` loses its `_id`, leaves `client_contact`, and the product vocabulary
		// maps `contact` to `client` — so the label read "Client client".
		expect(fieldLabel('client_contact_id')).toBe('Client');
		expect(fieldLabel('contact_id')).toBe('Client');
		expect(fieldLabel('assignee_contact_id')).toBe('Assignee client');
	});
});

describe('the expanded detail', () => {
	const event = (metadata: ActionEvent['metadata']): ActionEvent => ({
		id: 1,
		event_type: 'client.updated',
		created_at: '2026-09-23T09:15:00Z',
		method: 'PATCH',
		route: '/firm/contacts/{contact_uid}',
		status: 200,
		path_params: { contact_uid: 'c1' },
		metadata,
		entities: [],
		actor_id: 'u-1',
		actor_sub: 'user_1',
		actor_email: null,
		actor_name: 'Alice Feed',
		tenant_id: 1,
		tenant_uid: 'ten_1',
		tenant_name: 'Clifton Wealth',
		target_tenant_id: null,
		target_tenant_uid: null,
		target_tenant_name: null
	});

	it('states every changed field, where the headline names only three', () => {
		const rows = detailRows(
			event({ correlation_id: 'c', changed: ['address', 'dob', 'email', 'phone', 'nationality'] })
		);
		expect(rows[0]).toEqual(['Fields changed', 'address, dob, email, phone, nationality']);
	});

	it('uses the singular for one field', () => {
		const rows = detailRows(event({ correlation_id: 'c', changed: ['email'] }));
		expect(rows[0]).toEqual(['Field changed', 'email']);
	});

	it('never renders the raw list as well', () => {
		// `changed` is on SHOWN_ELSEWHERE, so the generic metadata loop must not repeat it as a
		// JSON preview underneath its own readable row.
		const rows = detailRows(event({ correlation_id: 'c', changed: ['email'] }));
		expect(rows.filter(([label]) => label.toLowerCase().includes('changed'))).toHaveLength(1);
	});
});

describe('an update says what the field WAS', () => {
	// `metadata.changes` is the api's diff of two reads of the row (`app/audit_actions.py::diff_rows`).
	// It is the only capture that holds both sides of the same fact: `changed` gives names,
	// `body` gives what the request asked for, and neither is the outcome.
	const withChanges = (changes: unknown, extra: Record<string, unknown> = {}): ActionEvent => ({
		id: 1,
		event_type: 'client.vulnerability_updated',
		created_at: '2026-09-23T09:15:00Z',
		method: 'PUT',
		route: '/crm/contacts/{contact_uid}/vulnerability',
		status: 200,
		path_params: {},
		metadata: { changes, ...extra },
		entities: [{ kind: 'contact', id: '1', uid: 'c1', label: 'Theo Caldwell' }],
		actor_id: 'u-1',
		actor_sub: 'user_1',
		actor_email: null,
		actor_name: 'Alice Feed',
		tenant_id: 1,
		tenant_uid: 'ten_1',
		tenant_name: 'Clifton Wealth',
		target_tenant_id: null,
		target_tenant_uid: null,
		target_tenant_name: null
	});

	it('reads both sides of every column that moved', () => {
		const rows = changeRows(
			withChanges({
				vulnerability_notes: { from: 'Hearing loss', to: 'Hearing loss, recent bereavement' }
			})
		);
		expect(rows).toContainEqual({
			label: 'Vulnerability notes',
			from: 'Hearing loss',
			to: 'Hearing loss, recent bereavement'
		});
	});

	it('says a field was EMPTY rather than dropping that half', () => {
		// "Balanced" alone cannot be told from "(not set) → Balanced", and the difference is
		// whether this was a correction or a first answer.
		const rows = changeRows(withChanges({ retirement_age: { from: null, to: 67 } }));
		expect(rows).toContainEqual({ label: 'Retirement age', from: NOT_SET, to: '67' });
	});

	it('says a field was CLEARED', () => {
		const rows = changeRows(withChanges({ retirement_age: { from: 67, to: null } }));
		expect(rows).toContainEqual({ label: 'Retirement age', from: '67', to: NOT_SET });
	});

	it('states an overflowing tail as a count, with no invented before-value', () => {
		const rows = changeRows(
			withChanges({ _omitted: { from: null, to: '12 further columns changed' } })
		);
		expect(rows).toContainEqual({
			label: 'Also changed',
			from: null,
			to: '12 further columns changed'
		});
	});

	it('keeps the body from repeating what the diff already says, under either name', () => {
		// The wire sends `notes`; the column is `vulnerability_notes`. The two are the same fact,
		// and the change row is the half that carries the before-value.
		const rows = bodyFields(
			withChanges(
				{ vulnerability_notes: { from: 'old', to: 'new' } },
				{ body: { notes: 'new', reason_code: 'health' } }
			)
		);
		expect(rows.map(([label]) => label)).toEqual(['Reason code']);
	});

	it('ignores a malformed changes blob rather than rendering half of it', () => {
		expect(recordedChanges(withChanges('not an object'))).toEqual([]);
		expect(recordedChanges(withChanges({ col: 'not a pair' }))).toEqual([]);
		expect(recordedChanges(withChanges(null))).toEqual([]);
	});

	it('does not print the raw map as a detail row', () => {
		const labels = detailRows(withChanges({ a: { from: 1, to: 2 } })).map(([label]) => label);
		expect(labels).not.toContain('Changes');
	});
});
