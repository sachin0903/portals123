import { redirect } from '@sveltejs/kit';
import type { AppExecutionRow, FlowOut } from '@graphene/ui/book/journeys';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

/** The platform's `/journeys` inbox for this firm, streamed. The start menu (published flows)
 *  only exists for an operator who may write — `./new` refuses anyone else anyway. */
export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	return { inbox: loadInbox(fetch, locals.accessToken, tenant.uid, writing) };
};

async function loadInbox(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	tenantUid: string,
	canStart: boolean
) {
	const [applications, flows] = await Promise.all([
		targetJson<AppExecutionRow[]>(fetch, accessToken, tenantUid, '/applications'),
		canStart
			? targetJson<FlowOut[]>(fetch, accessToken, tenantUid, '/flows', { status: 'published' })
			: null
	]);
	return {
		applications: applications.ok ? applications.data : [],
		loadFailed: !applications.ok,
		startFlows: canStart && flows?.ok ? flows.data : []
	};
}
