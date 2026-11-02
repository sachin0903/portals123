<script lang="ts">
	import { enhance } from '$app/forms';
	import { Alert, AlertDescription } from '@graphene/ui';
	import { TaskDetail } from '@graphene/ui/book/tasks';
	import type { PageData } from './$types';

	let { data, form }: { data: PageData; form: { error?: string } | null } = $props();
	const base = $derived(`/tenants/${data.tenant.uid}/operate`);
</script>

<svelte:head><title>{data.task.title} · {data.tenant.name}</title></svelte:head>

{#if data.activityFailed}
	<Alert variant="destructive" class="mb-4">
		<AlertDescription>The task loaded, but its activity could not be loaded.</AlertDescription>
	</Alert>
{/if}

<TaskDetail
	task={data.task}
	activity={data.activity}
	assignees={data.assignees}
	links={{
		tasks: () => `${base}/tasks`,
		journey: (uid) => `${base}/journeys/${uid}`,
		// The operate portal's own client record, the shape every other operate mount uses.
		client: (uid) => `${base}/clients/${uid}`
	}}
	actions={data.writing
		? {
				enhance,
				complete: '?/complete',
				start: '?/start',
				cancel: '?/cancel',
				edit: '?/edit'
			}
		: null}
	formError={form?.error ?? null}
/>
