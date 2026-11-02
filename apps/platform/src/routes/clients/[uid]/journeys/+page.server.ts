import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import type { ClientJourneySummary } from '@graphene/ui';
import type { FlowOut } from '@graphene/ui/book/journeys';
import { apiUrl, authHeaders } from '$lib/api';
import { startJourneyLinks } from '$lib/start-journey';
import type { PageServerLoad } from './$types';

/** This client's journeys, and the published flows they may start — one streamed answer. */
export type JourneysBook = {
	journeys: ClientJourneySummary[];
	newJourneys: { code: string; name: string; href: string }[];
	/** A failed journeys read. An empty `journeys` beside this is "could not load", never "none yet". */
	loadError: boolean;
};

const failed = (): JourneysBook => ({ journeys: [], newJourneys: [], loadError: true });

export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	// Synchronous, and before `parent()`: a caller who may not see journeys is refused without
	// waiting for the shell or calling the API. Thrown here, not inside the streamed promise —
	// `error` has to happen before the response starts.
	if (!can(locals.permissions, locals.role, 'cases:read')) error(403, 'Not authorised');
	const canStart = can(locals.permissions, locals.role, 'cases:write');

	// The shell is the layout's own await (the sidebar cannot paint without it). Reading it here
	// names the client and does not add a round trip; it is the only thing this load waits for.
	// Both reads then stream as ONE promise, so the section's frame is on screen while they run
	// and a failed journeys read is `loadError` rather than an empty list that reads as "none yet".
	const { detail } = await parent();
	return { book: loadBook(locals.accessToken, fetch, detail.contact.uid, canStart) };
};

async function loadBook(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	uid: string,
	canStart: boolean
): Promise<JourneysBook> {
	const headers = authHeaders(accessToken);
	try {
		// Together, not one after the other: the published flows only matter to someone who may
		// start one, and only exist for them. A journeys failure is the page's error; a flows
		// failure only costs the "New journey" menu, which is the same as having nothing to start.
		const [res, flowsRes] = await Promise.all([
			fetch(`${apiUrl()}/crm/contacts/${uid}/journeys`, { headers }),
			canStart ? fetch(`${apiUrl()}/flows?status=published`, { headers }) : null
		]);
		if (!res.ok) return failed();
		const body = (await res.json()) as { journeys: ClientJourneySummary[] };
		const flows: FlowOut[] = flowsRes?.ok ? ((await flowsRes.json()) as FlowOut[]) : [];
		return {
			journeys: body.journeys ?? [],
			newJourneys: startJourneyLinks(flows, uid),
			loadError: false
		};
	} catch {
		return failed();
	}
}
