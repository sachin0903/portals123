<script lang="ts">
	// The firm console's chrome: who we are looking at, and the PLATFORM submenu over the
	// entities this custodian publishes for them.
	//
	// The submenu lives here rather than in the global sidebar because it is DATA — which
	// entities exist, and whether each has anything in it, is answered per firm by the
	// warehouse. A hard-coded nav would keep offering a tab after its model was retired, and
	// would not be able to say why an empty one is empty.
	import { Alert, AlertDescription, Badge, PageHeader } from '@graphene/ui';
	import { page } from '$app/state';

	let { data, children } = $props();

	const base = $derived(`/platforms/${page.params.slug}/firms/${page.params.uid}`);
	const current = $derived(page.url.pathname.split('/')[5] ?? '');

	/** Rows, or the reason there are none. Never a bare dash: see the module note. */
	function count(entity: (typeof data.entities)[number]): string {
		if (entity.availability !== 'ok') return '—';
		return entity.rows === null || entity.rows === undefined ? '?' : entity.rows.toLocaleString();
	}
</script>

<PageHeader
	title={data.firmName ?? data.uid}
	description="Records {data.provider.replace('_', ' ')} holds for this firm."
	back={{ href: `/platforms/${page.params.slug}`, label: 'Platform' }}
>
	{#snippet actions()}
		{#each data.sliceIds as id (id)}
			<Badge variant="ghost" class="font-mono text-[11px]">{id}</Badge>
		{/each}
	{/snippet}
</PageHeader>

{#if data.loadError}
	<Alert variant="destructive" class="mb-4">
		<AlertDescription>
			The entity list could not be read ({data.loadError}).
		</AlertDescription>
	</Alert>
{/if}

{#if !data.sliceIds.length && !data.loadError}
	<!-- Stated rather than silently showing every firm's rows: an unnarrowed console is a
	     misconfiguration, and it looks identical to a correctly-scoped one. -->
	<Alert class="mb-4">
		<AlertDescription>
			No firm identifier is set for {data.provider.replace('_', ' ')}. The records below may include
			other firms. Update this firm's platform settings before using them.
		</AlertDescription>
	</Alert>
{/if}

<div class="flex flex-col gap-6 lg:flex-row">
	<!-- The submenu. A flat list on its own, so the entity names read as a vocabulary rather
	     than as tabs competing with the page's own chrome. -->
	<nav aria-label="Platform entities" class="lg:w-56 lg:shrink-0">
		<p class="text-muted-foreground mb-2 text-xs font-medium">Platform</p>
		<ul class="space-y-0.5">
			{#each data.entities as entity (entity.key)}
				{@const active = current === entity.key}
				<li>
					<a
						href="{base}/{entity.key}"
						aria-current={active ? 'page' : undefined}
						class="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-sm
						       {active ? 'bg-accent text-accent-foreground font-medium' : 'hover:bg-accent/50'}
						       {entity.availability === 'ok' ? '' : 'text-muted-foreground'}"
						title={entity.reason || entity.note || undefined}
					>
						<span class="truncate">{entity.label}</span>
						<span class="text-muted-foreground shrink-0 text-xs tabular-nums">
							{count(entity)}
						</span>
					</a>
				</li>
			{/each}
		</ul>

		<!-- The write side, kept visually separate from the read tabs above: these leave the
		     warehouse and reach the live org, which is a different kind of thing to click.
		     Absent entirely for a platform we cannot write to yet: Seccl's client lives in its sync
		     service with its own Auth0 credentials and the API has no integration for it, so an
		     Actions page there would be an empty promise. -->
		{#if data.hasActions}
			<p class="text-muted-foreground mt-5 mb-2 text-xs font-medium">Actions</p>
			<ul class="space-y-0.5">
				<li>
					<a
						href="{base}/actions"
						aria-current={current === 'actions' ? 'page' : undefined}
						class="flex items-center rounded-md px-2 py-1.5 text-sm
					       {current === 'actions'
							? 'bg-accent text-accent-foreground font-medium'
							: 'hover:bg-accent/50'}"
					>
						Send to {data.provider.replace('_', ' ')}
					</a>
				</li>
			</ul>
		{/if}
	</nav>

	<div class="min-w-0 flex-1">
		{@render children?.()}
	</div>
</div>
