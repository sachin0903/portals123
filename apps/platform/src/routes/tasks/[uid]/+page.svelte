<script lang="ts">
	// Thin mount of the SHARED task record (@graphene/ui/book/tasks — the portal waterfall: the
	// admin portal mounts the same view read-only over its own loaders). Data loading and the form
	// actions stay in this app (+page.server.ts); the component gets the task, this portal's link
	// builders, and — because staff can act here — the action names + `enhance`.
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { RecordSkeleton, Streamed } from '@graphene/ui';
	import PageTitle from '$lib/components/page-title.svelte';
	import { TaskDetail } from '@graphene/ui/book/tasks';
	import type { PageData } from './$types';

	let { data, form }: { data: PageData; form: { error?: string } | null } = $props();
</script>

<Streamed data={data.record} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton cards={2} />
	{/snippet}
	{#snippet children(record)}
		<PageTitle title="{record.task.title} · Tasks · Platform" />
		<TaskDetail
			task={record.task}
			activity={record.activity}
			assignees={record.assignees}
			links={{
				tasks: () => '/tasks',
				journey: (id) => `/journeys/${id}`,
				// By CONTACT uid — the task row's contact ids are serials, which this route cannot resolve.
				client: (uid) => `/clients/${uid}`
			}}
			actions={{
				enhance,
				complete: '?/complete',
				...(data.canManage ? { start: '?/start', cancel: '?/cancel', edit: '?/edit' } : {})
			}}
			formError={form?.error ?? null}
		/>
	{/snippet}
</Streamed>
