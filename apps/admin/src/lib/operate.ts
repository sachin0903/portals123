import { fail } from '@sveltejs/kit';
import { authHeaders, detail, jsonHeaders } from '$lib/api';
import { targetTenantUrl, type TargetParams } from '$lib/target-tenant';
import { can } from '$lib/permissions';

export type OperationLocals = {
	permissions: readonly string[];
	role: string | null;
};

export const canOperateWrite = (locals: OperationLocals): boolean =>
	can(locals.permissions, locals.role, 'tenant_portal:write');

export async function targetRequest(
	fetch: typeof globalThis.fetch,
	token: string | null,
	tenantUid: string,
	path: string,
	init: RequestInit = {},
	params: TargetParams = {}
): Promise<Response> {
	return fetch(targetTenantUrl(path, tenantUid, params), {
		...init,
		headers:
			init.body instanceof FormData
				? authHeaders(token)
				: { ...authHeaders(token), ...init.headers }
	});
}

export async function targetJson<T>(
	fetch: typeof globalThis.fetch,
	token: string | null,
	tenantUid: string,
	path: string,
	params: TargetParams = {}
): Promise<{ ok: true; data: T } | { ok: false; status: number }> {
	try {
		const response = await targetRequest(fetch, token, tenantUid, path, {}, params);
		return response.ok
			? { ok: true, data: (await response.json()) as T }
			: { ok: false, status: response.status };
	} catch {
		return { ok: false, status: 0 };
	}
}

export async function targetMutation(
	fetch: typeof globalThis.fetch,
	token: string | null,
	tenantUid: string,
	path: string,
	init: RequestInit = {}
): Promise<Response> {
	return fetch(targetTenantUrl(path, tenantUid), {
		...init,
		headers: init.body instanceof FormData ? authHeaders(token) : jsonHeaders(token)
	});
}

export const readOnlyFailure = () =>
	fail(403, { error: 'This tenant portal is open in read-only mode.' });

export async function mutationFailure(response: Response, fallback: string) {
	return fail(response.status, { error: (await detail(response)) || fallback });
}
