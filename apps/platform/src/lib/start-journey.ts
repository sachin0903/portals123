import type { FlowOut } from '@graphene/ui/book/journeys';

/** The kickoff door for one flow, with the client already chosen when the start is from
 *  their record. One builder so the record's menu, its Journeys tab and the journeys listing
 *  all open the same page the same way. */
export function kickoffHref(flowCode: string, clientUid?: string | null): string {
	const params = new URLSearchParams({ flow: flowCode });
	if (clientUid) params.set('client', clientUid);
	return `/journeys/new?${params.toString()}`;
}

/** The "New journey" entries for a client's record: every published flow, pointed at this
 *  client. */
export function startJourneyLinks(
	flows: readonly Pick<FlowOut, 'code' | 'name'>[],
	clientUid: string
): { code: string; name: string; href: string }[] {
	return flows.map((flow) => ({
		code: flow.code,
		name: flow.name,
		href: kickoffHref(flow.code, clientUid)
	}));
}
