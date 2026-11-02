<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { RecordSkeleton, Streamed } from '@graphene/ui';
	import KickoffWizard from './kickoff-wizard.svelte';
	import type { PageData } from './$types';

	let {
		data,
		form
	}: { data: PageData; form: { error?: string; liveJourneyUid?: string | null } | null } = $props();
</script>

<svelte:head><title>New journey · Platform</title></svelte:head>

<Streamed data={data.wizard} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton cards={3} />
	{/snippet}
	{#snippet children(wizard)}
		<KickoffWizard {wizard} {form} canCreateIllustrationClient={data.canCreateIllustrationClient} />
	{/snippet}
</Streamed>
