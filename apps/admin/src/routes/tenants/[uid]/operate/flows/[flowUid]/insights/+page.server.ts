import { redirect } from '@sveltejs/kit';
import type { FlowInsights } from '@graphene/ui/book/journeys';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

const WINDOWS = [30, 90, 180, 365];
export const load: PageServerLoad = async ({ locals, fetch, parent, params, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const requested = Number(url.searchParams.get('days'));
	const days = WINDOWS.includes(requested) ? requested : 90;
	const query = new URLSearchParams({
		since: new Date(Date.now() - days * 86_400_000).toISOString()
	});
	const version = url.searchParams.get('version');
	if (version) query.set('version', version);
	const result = await targetJson<FlowInsights>(
		fetch,
		locals.accessToken,
		tenant.uid,
		`/flows/${params.flowUid}/insights`,
		query
	);
	return {
		insights: result.ok ? result.data : null,
		loadFailed: !result.ok && result.status !== 404,
		days,
		flowId: params.flowUid
	};
};
