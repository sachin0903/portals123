import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { buildTemplateUsage } from '$lib/email-template-usage';
import { templateScope } from '$lib/email-templates/scope';
import { can } from '$lib/permissions';
import type { CatalogueTrigger, NotificationRule } from '@graphene/ui';
import type { RequestHandler } from './$types';

/** Notification-rule usage for the email-template delete confirm.
 *
 *  Loaded when the confirm opens, not on first paint: the listing does not need this map to draw. */
export const GET: RequestHandler = async ({ locals, fetch, params }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const scope = await templateScope(params.uid, locals.accessToken, fetch);
	if (!can(locals.permissions, locals.role, scope.read)) {
		return json({ error: 'Not permitted.' }, { status: 403 });
	}
	const headers = authHeaders(locals.accessToken);
	const [rulesRes, notifCatRes] = await Promise.all([
		fetch(`${apiUrl()}/admin/tenants/${params.uid}/notification-rules`, { headers }),
		fetch(`${apiUrl()}/admin/notification-catalogue`, { headers })
	]);
	const rules = (rulesRes.ok ? await rulesRes.json() : []) as NotificationRule[];
	const notifCatalogue = (notifCatRes.ok ? await notifCatRes.json() : []) as CatalogueTrigger[];
	return json(buildTemplateUsage(rules, notifCatalogue));
};
