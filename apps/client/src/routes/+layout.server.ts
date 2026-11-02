import { redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { ACTIVE_CONTACT_COOKIE, cookieOptions } from '$lib/server/workos';
import type { Cookies } from '@sveltejs/kit';
import type { NotificationItemData } from '@graphene/ui';
import type { LayoutServerLoad } from './$types';

/** Just enough of `/applications/me` to tell whether a journey is still running, which drives the
 * conditional "Onboarding" nav entry. `stages` is the whole-journey milestone rollup — the client's
 * own steps plus the staff-only tiers they wait on — and is what the tab keys on; `steps` is their
 * own work alone, kept as the fallback for an API build that predates the rollup. */
type MyOnboarding = {
	steps: { status: string }[];
	done: number;
	total: number;
	stages?: { status: 'done' | 'current' | 'upcoming' }[];
	flow_name?: string | null;
	flow_code?: string | null;
};

/** One client this login may act for — the active-contact switcher's option list.
 *
 *  Both keys, deliberately: the switcher selects (and the cookie stores) the serial `id`, which is
 *  also what the `?contact_id=` slice filters take; `uid` is what a URL PATH takes, since no API
 *  path names a serial any more (docs/api.md → "Addressing"). */
export type MyContact = { id: number; uid: string; name: string };

/** The linked contacts this login may act for, and which one is active right now.
 *
 * A login is linked to more than one `crm.contacts` row only rarely (a guardian/delegate
 * acting for more than one person), but when it is, every page must agree on which one is
 * "active" — the switcher in the nav bar picks it, an httpOnly cookie carries the choice
 * across page loads (it can't live in the URL or in browser-only memory: every page's data
 * comes from a fresh server-side `load()` call, which has no memory of a previous request —
 * see the ACTIVE_CONTACT_COOKIE doc comment). `GET /crm/contacts/mine` is the source of truth
 * for both the option list and the alphabetically-first default; the cookie is only ever
 * trusted here after checking it against that same list — a stale/tampered value just falls
 * back to the default, and every downstream API call re-validates it again regardless. */
async function loadActiveContact(
	accessToken: string | null | undefined,
	fetchFn: typeof fetch,
	cookies: Cookies
): Promise<{
	contacts: MyContact[];
	activeContactId: number | null;
	activeContactUid: string | null;
}> {
	if (!accessToken) return { contacts: [], activeContactId: null, activeContactUid: null };
	try {
		const res = await fetchFn(`${apiUrl()}/crm/contacts/mine`, {
			headers: authHeaders(accessToken)
		});
		if (!res.ok) return { contacts: [], activeContactId: null, activeContactUid: null };
		const body = (await res.json()) as {
			contacts: MyContact[];
			default_contact_id: number | null;
		};
		const contacts = body.contacts ?? [];
		const cookieValue = cookies.get(ACTIVE_CONTACT_COOKIE);
		const cookieId = cookieValue ? Number.parseInt(cookieValue, 10) : null;
		const validCookie =
			cookieId !== null && contacts.some((c) => c.id === cookieId) ? cookieId : null;
		const activeContactId = validCookie ?? body.default_contact_id ?? null;
		// Pin the resolved default into the cookie the first time, so it doesn't silently drift
		// to a different "alphabetically first" contact later if the book changes.
		if (validCookie === null && activeContactId !== null) {
			cookies.set(ACTIVE_CONTACT_COOKIE, String(activeContactId), cookieOptions);
		}
		// Resolved from the option list rather than read as a second field, so the two can never
		// name different contacts: whatever `activeContactId` settled on is the row this uid is from.
		const activeContactUid = contacts.find((c) => c.id === activeContactId)?.uid ?? null;
		return { contacts, activeContactId, activeContactUid };
	} catch {
		return { contacts: [], activeContactId: null, activeContactUid: null };
	}
}

/** The bell's initial state: recent notifications + the unread count. Best-effort — a failure
 * yields an empty bell, never a broken shell; the badge self-heals on its next poll. */
async function loadNotifications(
	accessToken: string | null | undefined,
	fetchFn: typeof fetch,
	contactId: number | null
): Promise<{ items: NotificationItemData[]; unread: number }> {
	if (!accessToken) return { items: [], unread: 0 };
	const cid = contactId !== null ? `?contact_id=${contactId}&` : '?';
	try {
		const feed = await fetchFn(`${apiUrl()}/notifications${cid}limit=8`, {
			headers: authHeaders(accessToken)
		});
		if (!feed.ok) return { items: [], unread: 0 };
		const body = (await feed.json()) as { items?: NotificationItemData[]; unread_count?: number };
		return {
			items: body.items ?? [],
			unread: typeof body.unread_count === 'number' ? body.unread_count : 0
		};
	} catch {
		return { items: [], unread: 0 };
	}
}

/** Surface the signed-in user + per-tenant branding to the layout (shell logo/name/copy), plus
 * whether onboarding has steps outstanding so the nav can show an "Onboarding" tab only when
 * relevant. */
export const load: LayoutServerLoad = async ({ locals, fetch, cookies, depends }) => {
	depends('app:shell');
	// The wrong-portal gate is DECIDED in hooks.server.ts (so `+server.ts` endpoints are covered
	// too — a layout load never runs for those); this is where a page's decision becomes the
	// redirect it needs. The hook has already ended a wrong-portal session (cookie + WorkOS).
	if (locals.gate === 'wrong_portal') redirect(303, '/login?wrong_portal=1');

	const { contacts, activeContactId, activeContactUid } = await loadActiveContact(
		locals.accessToken,
		fetch,
		cookies
	);
	const journey = await loadOnboarding(
		locals.accessToken,
		fetch,
		Boolean(locals.user),
		activeContactId
	);
	// Onboarding is a focused journey. Do not fetch a feed that the shell will not render, and do
	// not start the bell from a server-rendered unread count that can flash before hydration.
	const notifications = journey.active
		? { items: [], unread: 0 }
		: await loadNotifications(locals.accessToken, fetch, activeContactId);
	return {
		user: locals.user ? { email: locals.user.email } : null,
		// A member of staff looking at this portal as the client (hooks.server.ts). The layout
		// draws the banner from it and withholds the chrome that would act for the client.
		viewAs: locals.viewAs,
		branding: locals.branding,
		appName: locals.appName,
		onboarding: journey.active,
		/** The live journey's own name, when it is not account opening. */
		journeyTitle: journey.title,
		notifications: notifications.items,
		notificationsUnread: notifications.unread,
		contacts,
		activeContactId,
		activeContactUid,
		traceId: locals.traceId,
		spanId: locals.spanId
	};
};

/** Best-effort: a failure must never break the shell, so swallow it and hide the tab.
 *
 * `contactId` is the active contact, and it is not optional in practice. This flag does not just
 * add a tab — it REPLACES the whole nav bar with Onboarding (`$lib/nav.ts`) and sends `/` to the
 * onboarding page (`+page.server.ts`). Asked login-wide, one linked contact still onboarding
 * answered `true` for every contact behind that login, so switching to a fully-onboarded client
 * landed back on the other one's application, and the switcher's own destination is `/` — which
 * redirected there again. The API ignores a `contact_id` that is not really the caller's. */
async function loadOnboarding(
	accessToken: string | null | undefined,
	fetchFn: typeof fetch,
	signedIn: boolean,
	contactId: number | null
): Promise<{ active: boolean; title: string | null }> {
	if (!signedIn || !accessToken) return { active: false, title: null };
	try {
		const res = await fetchFn(
			`${apiUrl()}/applications/me${contactId !== null ? `?contact_id=${contactId}` : ''}`,
			{
				headers: authHeaders(accessToken)
			}
		);
		if (!res.ok) return { active: false, title: null };
		const mine: MyOnboarding = await res.json();
		// The tab follows the JOURNEY, not the client's inbox. Keyed on a pending step of their own,
		// it disappeared the moment they finished their terms and reappeared when the adviser
		// released the next thing — so for most of a real application the client had no way back to
		// their own onboarding page except by typing the URL, and the page they were locked out of
		// is the one that says what is happening. Any unsettled milestone keeps it: an empty stage
		// list (no journey at all) still hides it, and a finished journey — every stage done —
		// hides it too.
		const stages = mine.stages ?? [];
		const active = stages.length
			? stages.some((s) => s.status !== 'done')
			: mine.steps.some((s) => s.status === 'pending');
		// Account opening keeps the name Onboarding. Any other single journey is titled as itself.
		const title =
			active && mine.flow_code && mine.flow_code !== 'onboarding' ? (mine.flow_name ?? null) : null;
		return { active, title };
	} catch {
		return { active: false, title: null };
	}
}
