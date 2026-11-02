/**
 * Server-side write gates on the tenant sub-pages (permission audit M2).
 *
 * The [id] layout admits `tenants:read`, and until this change every write ACTION under it
 * (members, roles, settings, environments, platforms, and the overview's storage provisioning)
 * ran with no server-side `tenants:write` check — the API refused the call, but the action's
 * shape leaked and the UI's error copy was whatever the API happened to say. Each module now
 * refuses a read-only operator itself, before any fetch; these tests pin one action per file
 * (they all share the same guard) plus the pass-through for a tenants:write holder.
 */
import { describe, expect, it } from 'vitest';
import { actions as overview } from './+page.server';
import { actions as api } from './settings/api/+page.server';
import { actions as environments } from './settings/sign-in/+page.server';
import { actions as members } from './members/+page.server';
import { actions as platforms } from './settings/platforms/+page.server';
import { actions as roles } from './roles/+page.server';
import { actions as sending } from './settings/sending/+page.server';
import { actions as settings } from './settings/+page.server';

const locals = (permissions: string[]) => ({
	user: { id: 'u-ops', email: 'ops@graphene.test' },
	accessToken: 'tok',
	permissions,
	role: null as string | null
});
const READ_ONLY = locals(['tenants:read']);
const WRITER = locals(['tenants:read', 'tenants:write']);

const request = (fields: Record<string, string>) => ({
	formData: async () => {
		const data = new FormData();
		for (const [k, v] of Object.entries(fields)) data.append(k, v);
		return data;
	}
});

const okJson = (body: unknown) =>
	new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'content-type': 'application/json' }
	});

/** Runs one action twice: read-only must 403 without touching the API; a writer must reach it. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function expectGated(action: any, event: Record<string, unknown>, apiBody: unknown = {}) {
	let touched = 0;
	const fetch = () => {
		touched += 1;
		return okJson(apiBody);
	};
	const refused = await action({ ...event, locals: READ_ONLY, fetch });
	expect(refused).toMatchObject({ status: 403 });
	expect(String((refused as { data: { error?: string } }).data.error)).toContain('tenants:write');
	expect(touched).toBe(0);

	const allowed = await action({ ...event, locals: WRITER, fetch });
	expect(allowed).not.toMatchObject({ status: 403 });
	expect(touched).toBeGreaterThan(0);
}

// The route param is the firm's UID, not its serial id: every action resolves the numeric id from
// the firm AFTER its write guard, so a read-only operator's action makes
// no API call at all — which is what these tests assert by counting fetches.
const TENANT_UID = 't_9f2ab1';
const PARAMS = { params: { uid: TENANT_UID } };

describe('tenant write actions refuse tenants:read and admit tenants:write', () => {
	it('overview — ?/provisionStorage (mints buckets/keys/catalogues)', async () => {
		await expectGated(overview.provisionStorage, PARAMS, { status: 'active', steps: {} });
	});

	// TEMPORARY, with the reseed card. It publishes a new flow version and replaces the seeded
	// forms' fields, so it is a write like any other and must refuse before it reaches the API.
	it('overview — ?/reseedOnboarding (republishes the built-in onboarding seed)', async () => {
		await expectGated(overview.reseedOnboarding, PARAMS, {
			flow_uid: 'f_1',
			created: false,
			updated: true,
			in_flight: 0,
			forms: 6
		});
	});

	it('overview — ?/resetData refuses before queueing the destructive job', async () => {
		let touched = 0;
		const fetch = () => {
			touched += 1;
			return okJson({ id: 1, status: 'queued' });
		};
		const event = {
			...PARAMS,
			request: request({ kind: 'reset_and_seed', confirm_alias: 'caldwell' })
		};
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const refused = await (overview.resetData as any)({ ...event, locals: READ_ONLY, fetch });
		expect(refused).toMatchObject({ status: 403 });
		expect(String(refused.data.dataError)).toContain('tenants:write');
		expect(touched).toBe(0);

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		await (overview.resetData as any)({ ...event, locals: WRITER, fetch });
		expect(touched).toBe(1);
	});

	it('overview — ?/setAi (the per-firm AI switch), with its own fail shape', async () => {
		// A firm cannot switch itself on: enabling AI actions is Graphene's decision as platform
		// provider (docs/aml.md's posture), so this action is operations-side and write-gated like
		// every other. Its refusal carries `aiError`, not the generic `error`, because the card
		// keys its own banner off that.
		let touched = 0;
		const fetch = () => {
			touched += 1;
			return okJson({ enabled: true, monthly_token_cap: 1000, tokens_this_month: 0 });
		};
		const event = {
			...PARAMS,
			request: request({ enabled: 'on', monthly_token_cap: '500000', notes: '' })
		};
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const refused = await (overview.setAi as any)({ ...event, locals: READ_ONLY, fetch });
		expect(refused).toMatchObject({ status: 403 });
		expect(String(refused.data.aiError)).toContain('tenants:write');
		expect(touched).toBe(0);

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const allowed = await (overview.setAi as any)({ ...event, locals: WRITER, fetch });
		expect(allowed).not.toMatchObject({ status: 403 });
		// ONE call: the endpoint takes the uid the route already carries, so the write is the only
		// thing the action does. It used to be two — a resolve, then the write — and the resolve is
		// what uid-addressing the API removed. The count that matters is still the 0 above: a
		// read-only operator's action touches the API not at all.
		expect(touched).toBe(1);
	});

	it('members — ?/addMember (and the guard is shared by every member action)', async () => {
		await expectGated(members.addMember, {
			...PARAMS,
			request: request({ email: 'new@firm.test', role_slug: 'adviser' })
		});
	});

	it('roles — ?/createRole', async () => {
		await expectGated(roles.createRole, {
			...PARAMS,
			request: request({ name: 'Ops', slug: 'ops', portal: 'platform' })
		});
	});

	it('settings — ?/editTenant (suspend/rename lives here)', async () => {
		await expectGated(settings.editTenant, {
			...PARAMS,
			request: request({ name: 'Caldwell Wealth', alias: 'caldwell' })
		});
	});

	it('settings — ?/setCurrencies', async () => {
		await expectGated(settings.setCurrencies, {
			...PARAMS,
			request: request({ base_currency: 'GBP', allowed_currencies: 'GBP' })
		});
	});

	it('api — ?/createKey (mints a partner-API secret)', async () => {
		await expectGated(
			api.createKey,
			{ ...PARAMS, request: request({ name: 'CRM', scope: 'clients:read' }) },
			{ key: { uid: 'k_1', name: 'CRM' }, secret: 'live_x' }
		);
	});

	it('environments — ?/saveConfiguration (the WorkOS registry)', async () => {
		await expectGated(environments.saveConfiguration, {
			...PARAMS,
			request: request({ workos_client_id: 'client_x' })
		});
	});

	it('platforms — ?/setProvider (fan-out enablement), with its own fail shape', async () => {
		let touched = 0;
		const fetch = () => {
			touched += 1;
			return okJson({ enabled: true, fanout: [] });
		};
		const event = {
			...PARAMS,
			request: request({ provider: 'seccl', enabled: 'true', source: 'node-1' })
		};
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const refused = await (platforms.setProvider as any)({ ...event, locals: READ_ONLY, fetch });
		expect(refused).toMatchObject({ status: 403 });
		// The card keys its error banner off `providerError` + `provider`, so the refusal must
		// carry that shape, not the generic `{ action, error }`.
		expect(refused.data).toMatchObject({ provider: 'seccl' });
		expect(String(refused.data.providerError)).toContain('tenants:write');
		expect(touched).toBe(0);

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const allowed = await (platforms.setProvider as any)({ ...event, locals: WRITER, fetch });
		expect(allowed).not.toMatchObject({ status: 403 });
		// ONE call: the endpoint takes the uid the route already carries, so the write is the only
		// thing the action does. It used to be two — a resolve, then the write — and the resolve is
		// what uid-addressing the API removed. The count that matters is still the 0 above: a
		// read-only operator's action touches the API not at all.
		expect(touched).toBe(1);
	});

	it('sending — ?/register (a firm sending domain)', async () => {
		await expectGated(sending.register, {
			...PARAMS,
			request: request({ domain: 'mail.example.co.uk' })
		});
	});
});
