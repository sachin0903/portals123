<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { RecordSkeleton, Streamed } from '@graphene/ui';
	import IllustrationComposer from './illustration-composer.svelte';
	import WrapperForm from './wrapper-form.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<svelte:head
	><title>New {data.illustrationType} illustration · {data.clientName}</title></svelte:head
>

<Streamed data={data.models} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton cards={2} />
	{/snippet}
	{#snippet children(models)}
		{#if data.illustrationType === 'SIPP'}
			<IllustrationComposer data={{ ...data, models }} />
		{:else}
			<WrapperForm
				data={{ ...data, models }}
				wrapper={data.illustrationType}
				available={data.wrappersEnabled}
			/>
		{/if}
	{/snippet}
</Streamed>
