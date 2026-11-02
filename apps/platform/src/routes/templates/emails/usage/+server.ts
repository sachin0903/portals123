import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import { buildTemplateUsage } from '$lib/email-template-usage';
import type { CatalogueTrigger, NotificationRule } from '@graphene/ui';
import type { RequestHandler } from './$types';

/** Notification-rule usage for the email-template delete confirm.
 *
 *  Loaded when the confirm opens, not on first paint of `/templates?kind=emails`: the listing does
 *  not need this map to draw, and a failed read must never stop the page loading. Best-effort: a
 *  403 here yields `{}` so the confirm still opens, just without the "these notifications fall
 *  back" warning. */
export const GET: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (!can(locals.permissions, locals.role, 'email_templates:read')) {
		return json({ error: 'Not permitted.' }, { status: 403 });
	}
	const headers = authHeaders(locals.accessToken);
	const [rules, catalogue] = await Promise.all([
		fetchJson<NotificationRule[]>(fetch, `${apiUrl()}/firm/notification-rules`, headers),
		fetchJson<CatalogueTrigger[]>(fetch, `${apiUrl()}/firm/notification-catalogue`, headers)
	]);
	return json(buildTemplateUsage(rules.ok ? rules.data : [], catalogue.ok ? catalogue.data : []));
};
