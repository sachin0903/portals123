import { error } from '@sveltejs/kit';
import {
	applyHoldingsSummary,
	readBankDetails,
	type ClientJourneySummary,
	type HoldingsSummary
} from '@graphene/ui';
import type { TaskDisplayRow } from '@graphene/ui/book/tasks';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { targetJson } from '$lib/operate';
import type { BookContactDetail } from '$lib/book/api';

type Task = TaskDisplayRow & { uid: string; title: string };

/**
 * The fat client record behind operate-as-firm's Overview and Details sections — one load, two
 * doors, the way the platform's `clients/[uid]` and `clients/[uid]/details` share
 * `client-record-page.svelte`. Streamed by both pages so the section is on screen the moment the
 * layout has named the client.
 */
export async function loadOperateClientRecord(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	tenantUid: string,
	clientUid: string,
	contactId: number | string
) {
	const [fatResult, summaryResult, taskResult, journeyResult] = await Promise.all([
		targetJson<BookContactDetail>(fetch, accessToken, tenantUid, `/crm/contacts/${clientUid}`),
		targetJson<HoldingsSummary>(fetch, accessToken, tenantUid, '/crm/holdings/summary', {
			contact_id: contactId
		}),
		targetJson<Task[]>(fetch, accessToken, tenantUid, '/tasks', {
			assignee: `contact:${contactId}`,
			limit: String(PAGE_SIZE)
		}),
		targetJson<{ journeys: ClientJourneySummary[] }>(
			fetch,
			accessToken,
			tenantUid,
			`/crm/contacts/${clientUid}/journeys`
		)
	]);
	if (!fatResult.ok) {
		if (fatResult.status === 404) error(404, 'Client not found.');
		error(fatResult.status || 500, 'Could not load the client.');
	}
	const detail = fatResult.data;
	const clientAum = detail.accounts.reduce((sum, account) => sum + (account.value ?? 0), 0);
	const { allocations, cash } = applyHoldingsSummary(
		summaryResult.ok ? summaryResult.data : null,
		clientAum
	);
	return {
		detail,
		allocations,
		cash,
		tasks: taskResult.ok ? taskResult.data : [],
		bankAccount: readBankDetails(detail.contact),
		holdingsError: !summaryResult.ok,
		journeys: journeyResult.ok ? (journeyResult.data.journeys ?? []) : []
	};
}
