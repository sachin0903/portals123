import { apiUrl, authHeaders } from '$lib/api';
import type {
	CatalogueTrigger,
	EmailTemplateOption,
	NotificationPreference,
	NotificationRule
} from '@graphene/ui';
import type { PageServerLoad } from './$types';

/** The firm's notification config (category defaults) + its authored rules + the builder catalogue
 * + the selectable email templates. The numeric tenant id comes from the [uid] section layout;
 * writes go through the sibling proxies (`save/+server.ts` for config, `rules/…` for rules). */
export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	const headers = authHeaders(locals.accessToken);
	const [cfgRes, rulesRes, catRes, tmplRes] = await Promise.all([
		fetch(`${apiUrl()}/admin/tenants/${params.uid}/notification-config`, { headers }),
		fetch(`${apiUrl()}/admin/tenants/${params.uid}/notification-rules`, { headers }),
		fetch(`${apiUrl()}/admin/notification-catalogue`, { headers }),
		fetch(`${apiUrl()}/admin/notification-email-templates`, { headers })
	]);
	return {
		config: cfgRes.ok ? ((await cfgRes.json()) as NotificationPreference[]) : [],
		rules: rulesRes.ok ? ((await rulesRes.json()) as NotificationRule[]) : [],
		catalogue: catRes.ok ? ((await catRes.json()) as CatalogueTrigger[]) : [],
		emailTemplates: tmplRes.ok ? ((await tmplRes.json()) as EmailTemplateOption[]) : []
	};
};
