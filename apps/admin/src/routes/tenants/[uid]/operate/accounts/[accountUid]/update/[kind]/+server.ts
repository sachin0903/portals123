import { json } from '@sveltejs/kit';
import { detail } from '$lib/api';
import { canOperateWrite, targetMutation } from '$lib/operate';
import type { RequestHandler } from './$types';

const TARGETS: Record<string, { path: (account: string) => string; method: 'PUT' | 'PATCH' }> = {
	model: { path: (account) => `/crm/accounts/${account}/model`, method: 'PUT' },
	fees: { path: (account) => `/crm/accounts/${account}/fee-packages`, method: 'PUT' },
	movements: { path: (account) => `/crm/accounts/${account}/regular-movements`, method: 'PUT' },
	// The ACCOUNT's own, not the client's. Resolution is account first, client second
	// (`app/bank_accounts.py`); writing the person from this card is what left the fallback
	// sentence up after a save — the read still said the account had none of its own.
	bank: { path: (account) => `/crm/accounts/${account}/bank-accounts`, method: 'PUT' }
};

export const PUT: RequestHandler = async (event) => update(event);
export const PATCH: RequestHandler = async (event) => update(event);

async function update({ locals, params, request, fetch }: Parameters<RequestHandler>[0]) {
	if (!locals.user || !canOperateWrite(locals))
		return json({ error: 'This tenant portal is read-only.' }, { status: 403 });
	const target = TARGETS[params.kind];
	if (!target) return json({ error: 'Unsupported account action.' }, { status: 404 });
	const payload = await request.json();
	const response = await targetMutation(
		fetch,
		locals.accessToken,
		params.uid,
		target.path(params.accountUid),
		{ method: target.method, body: JSON.stringify(payload) }
	);
	if (!response.ok) return json({ error: await detail(response) }, { status: response.status });
	return json(await response.json().catch(() => ({ ok: true })));
}
