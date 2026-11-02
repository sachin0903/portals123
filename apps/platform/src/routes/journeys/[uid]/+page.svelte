<script lang="ts">
	// Thin mount of the SHARED journey detail (@graphene/ui book/journeys — the portal waterfall:
	// the admin portal mounts the same record read-only over its own loaders). The loader and the
	// form actions stay in this app (+page.server.ts); the component gets the execution + tasks,
	// this portal's link builders, and — only when this user can write — the action names +
	// `enhance`. Absent those, every mutating control is hidden.
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { RecordSkeleton, REFRESH_WHILE, RefreshWhile, Streamed } from '@graphene/ui';
	import PageTitle from '$lib/components/page-title.svelte';
	import { checksInFlight, JourneyDetail, type JourneyExecution } from '@graphene/ui/book/journeys';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<Streamed data={data.record} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton cards={2} />
	{/snippet}
	{#snippet children(record)}
		{@const ex = record.execution as unknown as JourneyExecution}
		<RefreshWhile
			active={checksInFlight(ex.cases)}
			interval={REFRESH_WHILE.pendingCheck}
			onpoll={() => invalidateAll()}
		/>
		<PageTitle title="{ex.flow_name ?? 'Application'} · Platform" />
		<JourneyDetail
			execution={ex}
			tasks={record.tasks}
			staff={record.staff}
			links={{
				journeys: '/journeys',
				fillStep: (caseUid, stepKey, participantContactId) =>
					`/journeys/${ex.uid}/steps/${caseUid}/${stepKey}${participantContactId ? `?participant=${participantContactId}` : ''}`,
				document: (uid) => `/documents/${uid}`,
				applicationPdf: `/journeys/${ex.uid}/application.pdf`,
				termsCopy: (caseUid, stepKey, participantContactId) =>
					`/journeys/${ex.uid}/terms?case=${caseUid}&step=${stepKey}${participantContactId ? `&participant=${participantContactId}` : ''}`,
				client: (uid) => `/clients/${uid}`,
				account: (uid) => `/accounts/${uid}`
			}}
			actions={data.canManage
				? {
						enhance,
						action: '?/action',
						acknowledge: '?/acknowledge',
						retryStep: '?/retryStep',
						approveRun: '?/approveRun',
						identityDocument: '?/identityDocument',
						identityManual: '?/identityManual',
						cancel: '?/cancel',
						completeTask: '?/completeTask',
						assign: '?/assign',
						...(data.canInvitePortal ? { invitePortal: '?/invitePortal' } : {})
					}
				: null}
		/>
	{/snippet}
</Streamed>
