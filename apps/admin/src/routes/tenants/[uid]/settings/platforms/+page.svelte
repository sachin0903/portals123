<script lang="ts">
	import { refreshMessage } from '$lib/platforms/registry';
	import { Alert, AlertDescription } from '@graphene/ui';
	import type { ActionData, PageData } from './$types';
	import ProviderCard from './ProviderCard.svelte';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	const enablementOf = (key: string) => data.providers.find((p) => p.provider === key) ?? null;
	const fanout = $derived(form?.providerSet?.fanout?.[0]);
</script>

<!-- The heading is the Settings layout's; this panel is its Platforms section. -->
<div class="space-y-5">
	<p class="text-muted-foreground text-sm">
		Which platforms feed this firm's datalake, and the slice of the core warehouse that belongs to
		it.
	</p>
	{#if form?.providerError}
		<Alert variant="destructive">
			<AlertDescription>{form.providerError}</AlertDescription>
		</Alert>
	{/if}
	{#if form?.providerSet}
		<Alert>
			<AlertDescription>
				Saved{#if fanout?.tables}, fanned out: {Object.entries(fanout.tables)
						.map(([t, n]) => `${t} (${n})`)
						.join(', ')}{:else if fanout?.error}
					— fan-out: {fanout.error}{/if}.
			</AlertDescription>
		</Alert>
	{/if}
	{#if form?.refreshed}
		<!-- One wording, shared with the platform page's per-firm Refresh: they call the same
		     endpoint, and two hand-written descriptions of one outcome is how the same event comes
		     to read as two different events. -->
		<Alert variant={refreshMessage(form.refreshed).tone}>
			<AlertDescription>{refreshMessage(form.refreshed).text}</AlertDescription>
		</Alert>
	{/if}

	<div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
		{#each data.registry as entry (entry.provider)}
			<!-- canWrite is the layout's canManage (tenants:write): a read-only operator sees the
			     enablement state but the switch/save controls are disabled — the action and the API
			     refuse them anyway, so live-looking controls would only ever 403. -->
			<ProviderCard
				provider={entry.provider}
				meta={entry.meta}
				sources={data.sources[entry.provider] ?? []}
				options={data.options[entry.provider] ?? {}}
				enablement={enablementOf(entry.provider)}
				canWrite={data.canManage}
			/>
		{/each}
	</div>
</div>
