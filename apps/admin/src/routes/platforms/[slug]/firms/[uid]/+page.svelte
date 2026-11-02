<script lang="ts">
	// The firm console's landing view: every entity as a card, so the shape of what this
	// custodian holds for the firm is legible before choosing a tab.
	//
	// Deliberately NOT a redirect to the first entity. The single most useful thing here is the
	// overview — which entities have data, which have none, and which are not built — and a
	// redirect would hide exactly that behind whichever tab happened to sort first.
	import { Badge, Card, CardContent } from '@graphene/ui';
	import { page } from '$app/state';

	let { data } = $props();

	const base = $derived(`/platforms/${page.params.slug}/firms/${page.params.uid}`);

	const TONE: Record<string, string> = {
		ok: '',
		not_built: 'border-dashed',
		unavailable: 'border-dashed'
	};
</script>

<svelte:head><title>{data.firmName ?? data.uid} · Platform data</title></svelte:head>

<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
	{#each data.entities as entity (entity.key)}
		<a href="{base}/{entity.key}" class="focus-visible:ring-ring rounded-lg focus-visible:ring-2">
			<Card class="h-full transition-colors hover:border-current/20 {TONE[entity.availability]}">
				<CardContent>
					<div class="flex items-start justify-between gap-2">
						<p class="font-medium">{entity.label}</p>
						{#if entity.availability === 'ok'}
							<p class="text-2xl font-semibold tabular-nums">
								{(entity.rows ?? 0).toLocaleString()}
							</p>
						{:else}
							<Badge variant="ghost" class="shrink-0 text-[11px]">
								{entity.availability === 'not_built' ? 'Not built' : 'Unavailable'}
							</Badge>
						{/if}
					</div>

					<p class="text-muted-foreground mt-1 font-mono text-[11px]">{entity.relation}</p>

					<!-- The reason an entity is empty is the point of this card. An unexplained zero
					     and an unexplained blank read the same, and they mean opposite things. -->
					{#if entity.reason}
						<p class="text-muted-foreground mt-2 text-xs">{entity.reason}</p>
					{:else if entity.note}
						<p class="text-muted-foreground mt-2 text-xs">{entity.note}</p>
					{/if}

					{#if entity.missing_columns?.length}
						<p class="text-muted-foreground mt-2 text-xs">
							{entity.missing_columns.length} expected field(s) are unavailable.
						</p>
					{/if}
				</CardContent>
			</Card>
		</a>
	{/each}
</div>
