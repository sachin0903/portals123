<script lang="ts">
	// Operate-as-firm's mount of the client record for Overview and Details — the counterpart to
	// the platform's `client-record-page.svelte`. Read-only: operate has no client mutations, so
	// `actions` is null and the record renders without its edit affordances.
	import { invalidateAll } from '$app/navigation';
	import { ClientRecord, RecordSkeleton, Streamed, type ClientRecordView } from '@graphene/ui';
	import { dueLabel, isOpen, isOverdue } from '@graphene/ui/book/tasks';
	import type { loadOperateClientRecord } from '$lib/operate-client-record-load';

	type Record = Awaited<ReturnType<typeof loadOperateClientRecord>>;

	let {
		record,
		tenantUid,
		section
	}: { record: Promise<Record>; tenantUid: string; section: ClientRecordView } = $props();

	const base = $derived(`/tenants/${tenantUid}/operate`);
	const openTasks = (r: Record) =>
		r.tasks.filter(isOpen).map((task) => ({
			uid: task.uid,
			title: task.title,
			overdue: isOverdue(task),
			dueLabel: task.due_date ? dueLabel(task) : null
		}));
</script>

<Streamed data={record} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton kpis={section === 'overview' ? 4 : 0} cards={2} header={false} />
	{/snippet}
	{#snippet children(r)}
		<ClientRecord
			detail={r.detail}
			screeningChecks={r.detail.kyc}
			allocations={r.allocations}
			cash={r.cash}
			holdingsError={r.holdingsError}
			bankAccount={r.bankAccount}
			tasks={openTasks(r)}
			{section}
			links={{
				account: (uid) => `${base}/accounts/${uid}`,
				client: (uid) => `${base}/clients/${uid}`,
				review: (uid) => `${base}/reviews?open=${uid}`,
				document: (uid) => `${base}/documents/${uid}`,
				illustrations: `${base}/clients/${r.detail.contact.uid}/illustrations`,
				journey: (uid) => `${base}/clients/${r.detail.contact.uid}/journeys/${uid}`
			}}
			journeys={r.journeys}
			actions={null}
			onretry={() => invalidateAll()}
		/>
	{/snippet}
</Streamed>
