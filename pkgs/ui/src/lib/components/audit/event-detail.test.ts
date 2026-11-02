/**
 * Unit tests for the action-event detail helpers (`event-detail.ts`) — the pure logic behind the
 * expanded panel (changes list, entity rows, permission deltas) and the row headline, extracted
 * from the component so it is tested in its own right ahead of the master-detail split. The
 * component's SSR tests exercise these through the rendered output; this file pins the logic
 * directly, including the tricky cases those rails depend on (only-the-delta permissions, the
 * previous/subject change pairing, covered-id suppression, the id-not-value display).
 */
import { describe, expect, it } from 'vitest';
import type { ActionEvent } from './describe.js';
import {
	changeRows,
	coveredIds,
	correlationId,
	currentIds,
	detailRows,
	displayValue,
	entitiesOf,
	fieldLabel,
	headline,
	initialsOf,
	legacyPermissionsCount,
	metadataRecord,
	permissionsDiffOf,
	previousIds,
	queryParams,
	residualPathParams,
	targetEntities
} from './event-detail.js';

const event = (over: Partial<ActionEvent> = {}): ActionEvent => ({
	id: 1,
	event_type: 'client.created',
	created_at: '2026-08-03T09:15:00Z',
	method: 'POST',
	route: '/firm/contacts',
	status: 200,
	path_params: {},
	metadata: { correlation_id: 'corr_1' },
	actor_id: 'u-1',
	actor_sub: 'wos_alice',
	actor_email: 'alice@firm.test',
	actor_name: 'Alice Feed',
	tenant_id: 1,
	tenant_uid: 'ten_firm',
	tenant_name: 'Feed Firm',
	target_tenant_id: null,
	target_tenant_uid: null,
	target_tenant_name: null,
	...over
});

describe('initialsOf', () => {
	it('takes the first two word-initials, uppercased', () => {
		expect(initialsOf('Alice Feed')).toBe('AF');
	});
	it('splits emails on dots and @ so m.cole@… reads MC', () => {
		expect(initialsOf('m.cole@example.com')).toBe('MC');
	});
	it('copes with a single token and with empty input', () => {
		expect(initialsOf('Madonna')).toBe('M');
		expect(initialsOf('')).toBe('');
	});
});

describe('headline', () => {
	it('leads with the actor and folds the verb in lower-cased (actor-led)', () => {
		const { actor, rest } = headline(event());
		expect(actor).toBe('Alice Feed');
		expect(rest).toBe(' created a client');
	});
	it('attributes a message to the acting LOGIN, and names the client in the sentence', () => {
		// The WHO column is the audit answer: who actually did this. For a client-portal send that is
		// the login, which is an `auth.users` row acting FOR a `crm.contacts` one - so the row would
		// otherwise say a login sent a message and never say on whose behalf. The sentence carries
		// the client instead, which keeps the attribution honest and the row readable.
		const { actor, rest } = headline(
			event({
				event_type: 'message.sent',
				actor_name: 'Craig Currie',
				path_params: {},
				entities: [
					{ kind: 'contact', id: '8', label: 'Max Oakden' },
					{ kind: 'conversation_adviser', id: '1', label: 'Sarah Bell' }
				]
			})
		);
		expect(actor).toBe('Craig Currie');
		expect(rest).toBe(' sent a message to Sarah Bell as Max Oakden');
	});

	it('keeps the staff-portal message plain - the login IS the sender there', () => {
		const { actor, rest } = headline(
			event({
				event_type: 'message.sent',
				actor_name: 'Craig Currie',
				path_params: { conversation_id: 1 },
				entities: [
					{ kind: 'conversation', id: '1', label: 'Max Oakden' },
					{ kind: 'contact', id: '8', label: 'Max Oakden' }
				]
			})
		);
		expect(actor).toBe('Craig Currie');
		expect(rest).toBe(' sent a message to Max Oakden');
	});

	it('weaves a role change into the rest as old → new', () => {
		const { actor, rest } = headline(
			event({
				event_type: 'member.portal_role_updated',
				path_params: { user_id: 'u-77', portal: 'platform' },
				metadata: { subject: { role_slug: 'compliance' }, previous: { role_slug: 'adviser' } },
				entities: [{ kind: 'user', id: 'u-77', label: 'm.cole@example.com' }]
			})
		);
		expect(actor).toBe('Alice Feed');
		expect(rest).toBe(" changed m.cole@example.com's platform role: Adviser → Compliance");
	});
});

describe('entitiesOf / coveredIds / residualPathParams', () => {
	it('returns the entities array, or [] when absent or null', () => {
		expect(entitiesOf(event())).toEqual([]);
		expect(entitiesOf(event({ entities: null }))).toEqual([]);
		const ents = [{ kind: 'contact', id: '96', label: 'Margaret Cole' }];
		expect(entitiesOf(event({ entities: ents }))).toBe(ents);
	});
	it('coveredIds is the set of entity ids', () => {
		const e = event({ entities: [{ kind: 'contact', id: '96', label: 'M' }] });
		expect([...coveredIds(e)]).toEqual(['96']);
	});
	it('residualPathParams drops params already named by an entity row', () => {
		const e = event({
			path_params: { contact_id: '96', portal: 'platform' },
			entities: [{ kind: 'contact', id: '96', label: 'M' }]
		});
		// contact_id (96) is covered by the entity row; portal is not an id and stays.
		expect(residualPathParams(e)).toEqual([['portal', 'platform']]);
	});
});

describe('metadataRecord', () => {
	it('returns the subject / previous object, or {} when missing or mis-shaped', () => {
		const e = event({
			metadata: { subject: { a: 1 }, previous: ['nope'] as unknown as Record<string, unknown> }
		});
		expect(metadataRecord(e, 'subject')).toEqual({ a: 1 });
		expect(metadataRecord(e, 'previous')).toEqual({}); // an array is not a record
		expect(metadataRecord(event(), 'subject')).toEqual({});
	});
});

describe('permissions: delta vs legacy count', () => {
	const withBoth = event({
		metadata: {
			subject: { permissions: ['activity:read', 'contacts:read', 'contacts:write', 'tasks:read'] },
			previous: { permissions: ['contacts:read', 'contacts:write', 'flows:write', 'tasks:read'] }
		}
	});
	it('permissionsDiffOf is only the delta, sorted', () => {
		expect(permissionsDiffOf(withBoth)).toEqual({
			added: ['activity:read'],
			removed: ['flows:write']
		});
	});
	it('legacyPermissionsCount is null when a diff exists', () => {
		expect(legacyPermissionsCount(withBoth)).toBeNull();
	});
	it('legacyPermissionsCount is the count when there is no previous to diff against', () => {
		const legacy = event({
			metadata: { subject: { permissions: ['activity:read', 'contacts:read', 'flows:write'] } }
		});
		expect(permissionsDiffOf(legacy)).toBeNull();
		expect(legacyPermissionsCount(legacy)).toBe(3);
	});
});

describe('fieldLabel / displayValue', () => {
	it('fieldLabel strips id/slug suffixes and de-slugs', () => {
		expect(fieldLabel('role_slug')).toBe('Role');
		expect(fieldLabel('model_id')).toBe('Model');
		expect(fieldLabel('capacity')).toBe('Capacity');
	});
	it('displayValue resolves an entity id to its label', () => {
		const e = event({ entities: [{ kind: 'model', id: '9', label: 'Balanced 60/40' }] });
		expect(displayValue(e, 'model_id', 9)).toBe('Balanced 60/40');
	});
	it('displayValue de-slugs a slug value and counts a list', () => {
		expect(displayValue(event(), 'role_slug', 'compliance-officer')).toBe('Compliance officer');
		expect(displayValue(event(), 'permissions', ['a', 'b', 'c'])).toBe('3 permissions');
	});
});

describe('changeRows', () => {
	it('pairs previous and subject as from → to', () => {
		const e = event({
			metadata: { subject: { role_slug: 'compliance' }, previous: { role_slug: 'adviser' } }
		});
		expect(changeRows(e)).toEqual([{ label: 'Role', from: 'Adviser', to: 'Compliance' }]);
	});
	it('renders a previous-only field as "was old" (to is null)', () => {
		const e = event({ metadata: { previous: { model_id: 3 } } });
		expect(changeRows(e)).toEqual([{ label: 'Model', from: '3', to: null }]);
	});
	it('excludes the permissions list (the diff/count row owns it) and covered-id targets', () => {
		const e = event({
			metadata: {
				subject: { permissions: ['a', 'b'], user_id: 'u-77' }
			},
			entities: [{ kind: 'user', id: 'u-77', label: 'm.cole@example.com' }]
		});
		// permissions → excluded; user_id → already a covered entity row, so no change row at all.
		expect(changeRows(e)).toEqual([]);
	});
});

describe('previousIds / currentIds', () => {
	it('previousIds gathers the ids captured on the before-side', () => {
		const e = event({ metadata: { previous: { model_id: 3, ids: ['x', 'y'] } } });
		expect([...previousIds(e)].sort()).toEqual(['3', 'x', 'y']);
	});
	it('currentIds gathers path params and subject ids (the new state)', () => {
		const e = event({
			path_params: { account_id: '12' },
			metadata: { subject: { model_id: 9 } }
		});
		expect([...currentIds(e)].sort()).toEqual(['12', '9']);
	});
});

describe('correlationId / queryParams', () => {
	it('correlationId returns the id or null', () => {
		expect(correlationId(event())).toBe('corr_1');
		expect(correlationId(event({ metadata: {} }))).toBeNull();
	});
	it('queryParams returns the captured query entries, or []', () => {
		expect(queryParams(event({ metadata: { query: { tenant: 'ten_x' } } }))).toEqual([
			['tenant', 'ten_x']
		]);
		expect(queryParams(event())).toEqual([]);
	});
});

describe('targetEntities', () => {
	it('puts the SUBJECT of the event first, so the "Open …" button offers the right record', () => {
		// A document share names the document AND the client — the document is what was acted on.
		const e = event({
			event_type: 'document.viewed',
			entities: [
				{ kind: 'contact', id: '12', uid: 'con_a', label: 'Margaret Cole' },
				{ kind: 'document', id: 'doc_x', uid: 'doc_x', label: '2026 suitability report' }
			]
		});
		expect(targetEntities(e).map((x) => x.kind)).toEqual(['document', 'contact']);
	});

	it('drops entities with no uid — the recorded serial is history, not an address', () => {
		const e = event({
			event_type: 'client.updated',
			entities: [
				{ kind: 'account', id: '9', uid: null, label: 'ISA' },
				{ kind: 'contact', id: '12', uid: 'con_a', label: 'Margaret Cole' }
			]
		});
		expect(targetEntities(e).map((x) => x.uid)).toEqual(['con_a']);
	});

	it('keeps the resolved order when the resource has no entity kind of its own', () => {
		const e = event({
			event_type: 'message.sent',
			entities: [
				{ kind: 'contact', id: '12', uid: 'con_a', label: 'Margaret Cole' },
				{ kind: 'user', id: '3', uid: 'usr_b', label: 'alice@firm.test' }
			]
		});
		expect(targetEntities(e).map((x) => x.kind)).toEqual(['contact', 'user']);
	});

	it('is empty when nothing was resolved', () => {
		expect(targetEntities(event())).toEqual([]);
	});
});

describe('detailRows — the parsed replacement for the raw JSON', () => {
	const base = {
		id: 1,
		event_type: 'authentication.succeeded',
		created_at: '2026-09-17T11:54:00Z',
		method: 'POST',
		route: '/auth/succeeded',
		status: 200,
		path_params: {},
		actor_id: null,
		actor_sub: 'user_01KWHQ2K3G2ZS31EABKHXSEGTG',
		actor_email: 'craig@grapheneplatforms.com',
		actor_name: 'Craig Currie',
		tenant_id: 1,
		tenant_uid: 'ten_a',
		tenant_name: 'Graphene'
	} as unknown as ActionEvent;

	it('states what the event carries that nothing above already says', () => {
		// The defect this exists for: the raw-JSON block was the ONLY place a custom metadata key
		// could be read, so removing it left an authentication event showing a headline and four
		// rows of chrome — and nothing about the event itself.
		const rows = detailRows({ ...base, metadata: { method: 'password' } });
		expect(rows).toContainEqual(['Method', 'password']);
	});

	it('never repeats what the Metadata list or the change rows already state', () => {
		const rows = detailRows({
			...base,
			metadata: { correlation_id: 'corr_1', query: { page: 2 }, method: 'password' }
		});
		expect(rows.map(([label]) => label)).toEqual(['Method']);
	});

	it('leaves the SUBJECT to the change rows rather than saying it twice', () => {
		// `changeRows` already reads the captured subject — as "old to new" where a previous value
		// was pre-read, and as a plain value where it was not. Repeating it here would print every
		// changed field a second time under a different heading.
		const rows = detailRows({
			...base,
			metadata: { subject: { role_slug: 'compliance_officer' }, method: 'password' }
		});
		expect(rows).toEqual([['Method', 'password']]);
	});

	it('previews a blob rather than burying the rows around it', () => {
		const big = Object.fromEntries(
			Array.from({ length: 40 }, (_, i) => [`k${i}`, `value ${i} padded out a fair way`])
		);
		const [, value] = detailRows({ ...base, metadata: { payload: big } })[0] as [string, string];
		expect(value).toContain('40 fields');
	});

	it('is EMPTY when the event carries nothing extra, so no heading renders', () => {
		expect(detailRows({ ...base, metadata: {} })).toEqual([]);
	});
});

describe('changeRows de-duplicates against the entity rows', () => {
	const sent = {
		id: 9,
		event_type: 'document.sent',
		created_at: '2026-09-02T16:55:00Z',
		method: 'POST',
		route: '/documents/{uid}/send',
		status: 200,
		path_params: {},
		actor_id: null,
		actor_sub: 'user_1',
		actor_email: 'craig@grapheneplatforms.com',
		actor_name: 'Craig Currie',
		tenant_id: 1,
		tenant_uid: 'ten_a',
		tenant_name: 'Graphene',
		entities: [
			{ kind: 'document', id: 'HiA1I-3gXc8M', label: 'ID Verification — Driving Licence' }
		],
		metadata: { subject: { name: 'ID Verification — Driving Licence' } }
	} as unknown as ActionEvent;

	it('drops a captured value the entity row already names, even when it matched no id', () => {
		// The defect: the skip compared the captured value against entity IDs only. A document's
		// `name` holds its TITLE, never its uid, so "Document name — ID Verification" rendered
		// immediately above a DOCUMENT row reading the same words with a link on it.
		expect(changeRows(sent)).toEqual([]);
	});

	it('keeps a captured value no entity row states', () => {
		const rows = changeRows({
			...sent,
			metadata: { subject: { name: 'ID Verification — Driving Licence', channel: 'email' } }
		});
		expect(rows).toEqual([{ label: 'Channel', from: null, to: 'email' }]);
	});
});
