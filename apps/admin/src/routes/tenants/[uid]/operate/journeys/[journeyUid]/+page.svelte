<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { Alert, AlertDescription, REFRESH_WHILE, RefreshWhile } from '@graphene/ui';
	import { checksInFlight, JourneyDetail, type JourneyExecution } from '@graphene/ui/book/journeys';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const execution = $derived(data.execution as unknown as JourneyExecution);
	const base = $derived(`/tenants/${data.tenant.uid}/operate`);
</script>

<svelte:head><title>{execution.flow_name ?? 'Journey'} · {data.tenant.name}</title></svelte:head>

<RefreshWhile
	active={checksInFlight(execution.cases)}
	interval={REFRESH_WHILE.pendingCheck}
	onpoll={() => invalidateAll()}
/>

{#if data.tasksFailed}
	<Alert variant="destructive" class="mb-4">
		<AlertDescription
			>The journey loaded, but its related tasks could not be loaded.</AlertDescription
		>
	</Alert>
{/if}

<JourneyDetail
	{execution}
	tasks={data.tasks}
	staff={data.staff}
	links={{
		journeys: `${base}/journeys`,
		fillStep: (caseUid, stepKey, participantContactId) =>
			`${base}/journeys/${execution.uid}/steps/${caseUid}/${stepKey}${participantContactId ? `?participant=${participantContactId}` : ''}`,
		document: (uid) => `${base}/documents/${uid}`,
		applicationPdf: `${base}/journeys/${execution.uid}/application.pdf`
	}}
	actions={data.writing
		? {
				enhance,
				action: '?/action',
				acknowledge: '?/acknowledge',
				retryStep: '?/retryStep',
				cancel: '?/cancel',
				completeTask: '?/completeTask',
				assign: '?/assign'
			}
		: null}
/>
