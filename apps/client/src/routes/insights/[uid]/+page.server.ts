import { loginPath } from '@graphene/auth';
import { error, redirect } from '@sveltejs/kit';
import { relatedInsights, type NewsItem } from '@graphene/ui';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import { toPortalNews } from '$lib/page-data';
import type { PageServerLoad } from './$types';

export type InsightDetail = {
	uid: string;
	kind: string;
	title: string;
	slug: string;
	summary: string | null;
	published_at: string | null;
	hero_url: string | null;
	category_uid: string | null;
	category_name: string | null;
	has_file: boolean;
	body_html: string | null;
	file_content_type: string | null;
};

/** How many of the library to read for "Keep reading" — enough that the same topic can fill
 *  the three slots on a busy library, cheap enough to ride every article open. */
const RELATED_WINDOW = 12;
const RELATED_SHOWN = 3;

export const load: PageServerLoad = async ({ locals, fetch, params, parent, url }) => {
	if (!locals.user) redirect(303, loginPath(url));
	const { activeContactId } = await parent();
	const cid = activeContactId !== null ? `contact_id=${activeContactId}` : '';
	const headers = authHeaders(locals.accessToken);
	// The piece and the library in parallel: the library only feeds "Keep reading", so a failure
	// there costs the rail, never the article.
	const [res, library] = await Promise.all([
		fetchJson<InsightDetail>(
			fetch,
			`${apiUrl()}/crm/content/${params.uid}${cid ? `?${cid}` : ''}`,
			headers
		),
		fetchJson<{ items: Parameters<typeof toPortalNews>[0][] }>(
			fetch,
			`${apiUrl()}/crm/content?limit=${RELATED_WINDOW}${cid ? `&${cid}` : ''}`,
			headers
		)
	]);
	if (!res.ok) error(res.status === 404 ? 404 : 502, 'Not found');
	const item = res.data;
	const all: NewsItem[] = library.ok ? library.data.items.map(toPortalNews) : [];
	const current = toPortalNews({
		uid: item.uid,
		kind: item.kind,
		title: item.title,
		summary: item.summary,
		published_at: item.published_at,
		category_name: item.category_name,
		hero_url: item.hero_url
	});
	return { item, related: relatedInsights(all, current, RELATED_SHOWN) };
};
