<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { RecordSkeleton, Streamed } from '@graphene/ui';
	import StepFormBody from './step-form-body.svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<Streamed data={data.record} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton cards={2} />
	{/snippet}
	{#snippet children(record)}
		<StepFormBody data={{ ...data, ...record }} {form} />
	{/snippet}
</Streamed>
