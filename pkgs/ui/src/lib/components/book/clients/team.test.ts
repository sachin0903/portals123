import { describe, expect, it } from 'vitest';
import {
	CLIENT_TEAM_ROLES,
	CLIENT_TEAM_ROLE_ITEMS,
	CLIENT_TEAM_ROLE_LABELS,
	DEFAULT_CLIENT_TEAM_ROLE,
	canHoldClientTeamRole,
	clientAdviser,
	clientTeamPickerItems,
	clientTeamRoleLabel,
	isClientTeamRole,
	orderClientTeam,
	readClientTeamRole,
	replacedAdviserNote
} from './team';
import type { ClientAdviserRow, FirmMemberOption } from './types';

const platform = (status = 'active') => [{ portal: 'platform', status }];

const member = (over: Partial<FirmMemberOption> & { user_id: string }): FirmMemberOption => ({
	email: `${over.user_id}@firm.test`,
	name: over.user_id,
	is_adviser: false,
	types: ['staff'],
	portals: platform(),
	...over
});

const ANN = member({ user_id: 'ann', name: 'Ann Adviser', is_adviser: true });
const BOB = member({ user_id: 'bob', name: 'Bob Adviser', is_adviser: true });
const PAT = member({ user_id: 'pat', name: 'Pat Para' });
const CLIENT_LOGIN = member({
	user_id: 'cli',
	types: ['client'],
	portals: [{ portal: 'client', status: 'active' }]
});
const GONE = member({ user_id: 'gone', is_adviser: true, portals: platform('inactive') });
const MEMBERS = [ANN, BOB, PAT, CLIENT_LOGIN, GONE];

const row = (adviser_id: string, role: string, name = adviser_id): ClientAdviserRow => ({
	adviser_id,
	role,
	name,
	email: `${adviser_id}@firm.test`
});

describe('the team roles', () => {
	it('is the four roles, Adviser first and the default', () => {
		expect(CLIENT_TEAM_ROLES).toEqual(['adviser', 'paraplanner', 'administrator', 'support']);
		expect(DEFAULT_CLIENT_TEAM_ROLE).toBe('adviser');
		expect(CLIENT_TEAM_ROLE_ITEMS).toEqual([
			{ value: 'adviser', label: 'Adviser' },
			{ value: 'paraplanner', label: 'Paraplanner' },
			{ value: 'administrator', label: 'Administrator' },
			{ value: 'support', label: 'Support' }
		]);
		expect(Object.keys(CLIENT_TEAM_ROLE_LABELS)).toEqual([...CLIENT_TEAM_ROLES]);
	});

	it('knows its own members and nothing else', () => {
		for (const role of CLIENT_TEAM_ROLES) expect(isClientTeamRole(role)).toBe(true);
		for (const other of ['lead', 'advisor', 'Adviser', '', null, undefined, 3]) {
			expect(isClientTeamRole(other)).toBe(false);
		}
	});

	it('names a role, and never prints a stray value raw', () => {
		expect(clientTeamRoleLabel('paraplanner')).toBe('Paraplanner');
		expect(clientTeamRoleLabel('support')).toBe('Support');
		// A row the API has not migrated yet still reads as words.
		expect(clientTeamRoleLabel('servicing_adviser')).toBe('Servicing adviser');
		expect(clientTeamRoleLabel(null)).toBeNull();
		expect(clientTeamRoleLabel('  ')).toBeNull();
	});
});

describe('readClientTeamRole — what a form posted', () => {
	it('reads blank as the default, and one of the four as itself', () => {
		expect(readClientTeamRole(null)).toBe('adviser');
		expect(readClientTeamRole('')).toBe('adviser');
		expect(readClientTeamRole('  ')).toBe('adviser');
		expect(readClientTeamRole(' support ')).toBe('support');
	});

	it('refuses anything else, including the old free-text values', () => {
		for (const raw of ['lead', 'advisor', 'Adviser', 'owner']) {
			expect(readClientTeamRole(raw), raw).toBeNull();
		}
		expect(readClientTeamRole(new Blob(['adviser']))).toBeNull();
	});
});

describe('ordering the team', () => {
	it('puts the adviser first and keeps everyone else in order', () => {
		const team = [row('pat', 'paraplanner'), row('sam', 'support'), row('ann', 'adviser')];
		expect(orderClientTeam(team).map((r) => r.adviser_id)).toEqual(['ann', 'pat', 'sam']);
		expect(clientAdviser(team)?.adviser_id).toBe('ann');
		expect(clientAdviser([row('pat', 'paraplanner')])).toBeUndefined();
	});
});

describe('who can hold a role', () => {
	it('needs the adviser flag for Adviser', () => {
		expect(canHoldClientTeamRole(ANN, 'adviser')).toBe(true);
		expect(canHoldClientTeamRole(PAT, 'adviser')).toBe(false);
	});

	it('needs a member of staff for the other roles, adviser or not', () => {
		for (const role of ['paraplanner', 'administrator', 'support'] as const) {
			expect(canHoldClientTeamRole(PAT, role), role).toBe(true);
			expect(canHoldClientTeamRole(ANN, role), role).toBe(true);
			expect(canHoldClientTeamRole(CLIENT_LOGIN, role), role).toBe(false);
		}
	});

	it('reads `type` when `types` is absent, and platform access when both are', () => {
		const viaType = member({ user_id: 'x', types: undefined, type: ['client'] });
		expect(canHoldClientTeamRole(viaType, 'support')).toBe(false);
		const neither = member({ user_id: 'y', types: undefined });
		expect(canHoldClientTeamRole(neither, 'support')).toBe(true);
	});

	it('never offers someone whose platform access is not active', () => {
		for (const role of CLIENT_TEAM_ROLES) expect(canHoldClientTeamRole(GONE, role)).toBe(false);
	});
});

describe('the person picker', () => {
	const ids = (items: { value: string }[]) => items.map((i) => i.value);

	it('offers advisers for Adviser and staff for the rest', () => {
		expect(ids(clientTeamPickerItems(MEMBERS, [], 'adviser'))).toEqual(['ann', 'bob']);
		expect(ids(clientTeamPickerItems(MEMBERS, [], 'support'))).toEqual(['ann', 'bob', 'pat']);
	});

	it('leaves out only those who already hold THAT role', () => {
		const team = [row('ann', 'adviser'), row('pat', 'paraplanner')];
		// Ann is already the adviser; Bob may replace her.
		expect(ids(clientTeamPickerItems(MEMBERS, team, 'adviser'))).toEqual(['bob']);
		// Pat is already a paraplanner, so is not offered again for it…
		expect(ids(clientTeamPickerItems(MEMBERS, team, 'paraplanner'))).toEqual(['ann', 'bob']);
		// …but may be moved to Support, as may Ann.
		expect(ids(clientTeamPickerItems(MEMBERS, team, 'support'))).toEqual(['ann', 'bob', 'pat']);
	});

	it('labels a person by name and email, or email alone', () => {
		const unnamed = member({ user_id: 'u', name: null });
		expect(clientTeamPickerItems([ANN, unnamed], [], 'support')).toEqual([
			{ value: 'ann', label: 'Ann Adviser (ann@firm.test)' },
			{ value: 'u', label: 'u@firm.test' }
		]);
	});
});

describe('the note when a new adviser replaces the current one', () => {
	const team = [row('ann', 'adviser', 'Ann Adviser'), row('pat', 'paraplanner')];

	it('says the current adviser stays on as Support', () => {
		expect(replacedAdviserNote(team, 'adviser')).toBe('Ann Adviser stays on the team as Support.');
		expect(replacedAdviserNote(team, 'adviser', 'bob')).toBe(
			'Ann Adviser stays on the team as Support.'
		);
	});

	it('says nothing for another role, with no adviser, or when re-picking the adviser', () => {
		expect(replacedAdviserNote(team, 'support', 'bob')).toBeNull();
		expect(replacedAdviserNote([row('pat', 'paraplanner')], 'adviser', 'bob')).toBeNull();
		expect(replacedAdviserNote(team, 'adviser', 'ann')).toBeNull();
	});
});
