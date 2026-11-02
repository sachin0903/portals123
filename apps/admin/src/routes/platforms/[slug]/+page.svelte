<script lang="ts">
	// Platform overview: the four headline figures, the firms connected to this platform, and the
	// live activity rail. Tenants used to be their own nav item; they live here because "who is
	// connected" is a headline fact about a platform, not a separate area.
	import {
		Badge,
		Button,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		humanise,
		Money,
		Separator,
		StatusBadge
	} from '@graphene/ui';
	import EventFeed from '$lib/platforms/components/EventFeed.svelte';
	import RunHistogram from '$lib/platforms/components/RunHistogram.svelte';
	import TenantsPanel from '$lib/platforms/components/TenantsPanel.svelte';
	import { usePlatformStore } from '$lib/platforms/context';
	import { STATUS_PULSES, STATUS_TONE } from '$lib/platforms/status';

	let { data, form } = $props();

	const holder = usePlatformStore();
	const store = $derived(holder.store);
	const metrics = $derived(store.data.metrics);
	const recentRuns = $derived(store.data.runs.slice(0, 6));
	const streams = $derived(store.data.streams);
</script>

<div class="space-y-6">
	<!-- Headline figures. Nulls render "—": a relation that hasn't been promoted must not read
	     as a genuine zero. -->
	<div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
		<Card class="gap-2">
			<CardHeader><CardTitle class="text-sm">Clients</CardTitle></CardHeader>
			<CardContent>
				<div class="text-2xl font-semibold tabular-nums">
					{metrics?.clients?.toLocaleString('en-GB') ?? '—'}
				</div>
				<p class="text-muted-foreground mt-1 text-xs">on this platform</p>
			</CardContent>
		</Card>

		<Card class="gap-2">
			<CardHeader>
				<CardTitle class="text-sm">Assets under administration</CardTitle>
			</CardHeader>
			<CardContent>
				<div class="text-2xl font-semibold">
					{#if metrics?.aum == null}
						<span class="text-muted-foreground">—</span>
					{:else}
						<Money amount={metrics.aum} />
					{/if}
				</div>
				<p class="text-muted-foreground mt-1 text-xs">
					{metrics?.aum == null ? 'holdings unavailable' : 'current value of held positions'}
				</p>
			</CardContent>
		</Card>

		<Card class="gap-2">
			<CardHeader><CardTitle class="text-sm">Runs</CardTitle></CardHeader>
			<CardContent>
				{#if metrics}
					<RunHistogram {metrics} />
				{:else}
					<span class="text-muted-foreground text-2xl font-semibold">—</span>
				{/if}
			</CardContent>
		</Card>

		<Card class="gap-2">
			<CardHeader><CardTitle class="text-sm">Tenants</CardTitle></CardHeader>
			<CardContent>
				<div class="text-2xl font-semibold tabular-nums">{metrics?.tenants ?? 0}</div>
				<p class="text-muted-foreground mt-1 text-xs">firms connected</p>
			</CardContent>
		</Card>
	</div>

	<div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px] xl:items-start">
		<TenantsPanel {store} {form} />

		<div class="space-y-4">
			<Card class="gap-2">
				<CardHeader>
					<div class="flex items-center justify-between gap-2">
						<CardTitle class="text-base">Recent runs</CardTitle>
						<Button variant="ghost" size="sm" href={`/platforms/${store.data.meta.provider}/runs`}>
							All runs
						</Button>
					</div>
				</CardHeader>
				<CardContent>
					{#if recentRuns.length === 0}
						<p class="text-muted-foreground py-6 text-center text-sm">No runs yet.</p>
					{:else}
						<ul class="divide-border divide-y text-sm">
							{#each recentRuns as run (run.id)}
								<li class="flex items-center gap-2 py-2">
									<StatusBadge tone={STATUS_TONE[run.status]} pulse={STATUS_PULSES.has(run.status)}
										>{humanise(run.status)}</StatusBadge
									>
									<span class="min-w-0 flex-1 truncate">
										{run.models[0] ?? '—'}{run.models.length > 1
											? ` +${run.models.length - 1}`
											: ''}
									</span>
									<span class="text-muted-foreground shrink-0 tabular-nums">{run.started}</span>
									<span class="text-muted-foreground w-14 shrink-0 text-right tabular-nums">
										{run.duration ?? '—'}
									</span>
								</li>
							{/each}
						</ul>
					{/if}
				</CardContent>
			</Card>

			<Card class="gap-2">
				<CardHeader>
					<div class="flex items-center justify-between gap-2">
						<CardTitle class="text-base">Ingestion</CardTitle>
						<Button
							variant="ghost"
							size="sm"
							href={`/platforms/${store.data.meta.provider}/ingestion`}
						>
							View
						</Button>
					</div>
				</CardHeader>
				<CardContent>
					{#if streams.length === 0}
						<!-- Not "none configured": two of the three custodians ingest without streams at
						     all (a nightly SFTP drop, a scheduled poll), so an empty list here is the
						     normal state rather than something to fix. The Ingestion page says which. -->
						<p class="text-muted-foreground py-6 text-center text-sm">
							No live streams. See Ingestion for this provider's data schedule.
						</p>
					{:else}
						<ul class="divide-border divide-y text-sm">
							{#each streams as stream (stream.name)}
								<li class="flex items-center gap-2 py-2">
									<Badge
										variant={stream.status === 'live' ? 'default' : 'secondary'}
										class="font-normal"
									>
										{humanise(stream.status)}
									</Badge>
									<span class="min-w-0 flex-1 truncate font-mono text-xs">{stream.name}</span>
									<span class="text-muted-foreground shrink-0 text-xs tabular-nums">
										{stream.offset}
									</span>
								</li>
							{/each}
						</ul>
					{/if}
				</CardContent>
			</Card>

			<Card class="gap-2">
				<CardHeader><CardTitle class="text-base">Activity</CardTitle></CardHeader>
				<CardContent>
					<Separator class="mb-3" />
					<EventFeed script={store.data.feedScript} />
				</CardContent>
			</Card>
		</div>
	</div>

	<!-- WHAT THE MERGE DID. Asked before "what did it refuse", and until now unanswerable: the
	     ledger reached only `book_{uid}.merge_ledger`, a warehouse relation holding ONE run and
	     reachable only from the SQL console. A firm with no data, a firm whose chain never ran and
	     a firm whose daemon has no BOOK_WRITER_DSN all present as an empty book; this is the only
	     thing that separates them. -->
	{#if data.ledger}
		<Card class="gap-2">
			<CardHeader>
				<CardTitle class="text-sm">Merge runs</CardTitle>
			</CardHeader>
			<CardContent>
				{#if data.ledger.runs.length === 0}
					<p class="text-muted-foreground text-sm">
						No import has been recorded for this platform. If firms are enabled, check the provider
						service and start a refresh.
					</p>
				{:else}
					<ul class="divide-border divide-y text-sm">
						{#each data.ledger.runs as run (run.run_id)}
							<li class="py-2 first:pt-0 last:pb-0">
								<div class="flex items-start justify-between gap-4">
									<span>
										{#if run.tenant_uid}
											<a class="hover:underline" href="/tenants/{run.tenant_uid}"
												>{run.tenant_name ?? run.tenant_uid}</a
											>
										{:else}
											<span class="text-muted-foreground">unknown firm</span>
										{/if}
										<span class="text-muted-foreground ml-2 text-xs"
											>{new Date(run.run_at).toLocaleString('en-GB')}</span
										>
										{#if run.failed}
											<Badge variant="destructive" class="ml-2">{run.failed} failed</Badge>
										{/if}
									</span>
									<span class="text-muted-foreground shrink-0 tabular-nums text-xs">
										{run.created} created · {run.updated} updated
										{#if run.rejected}
											· <span class="text-destructive">{run.rejected} refused</span>
										{/if}
									</span>
								</div>
								<!-- Only the entities worth reading: an entity that wrote nothing and refused
								     nothing is the normal case and says nothing. -->
								{#each run.entities.filter((e) => e.note || e.rejected || Object.keys(e.reasons).length) as entity (entity.entity)}
									<div class="text-muted-foreground mt-1 pl-4 text-xs">
										<span class="font-mono">{entity.entity}</span>
										{#if entity.note}
											<span class="text-destructive ml-2">{entity.note}</span>
										{/if}
										{#each Object.entries(entity.reasons) as [reason, count] (reason)}
											<span class="ml-2">{reason} ({count})</span>
										{/each}
									</div>
								{/each}
							</li>
						{/each}
					</ul>
				{/if}
			</CardContent>
		</Card>
	{/if}

	<!-- WHAT THE MERGE COULD NOT WRITE. The question a row count cannot answer: a firm with no rows
	     and a firm whose rows were all refused look identical everywhere else, and the reasons are
	     the only thing that tells the two apart. Pooled, because 117 transactions refused for one
	     unmapped type is one thing to act on. -->
	{#if data.rejects}
		<Card class="gap-2">
			<CardHeader>
				<CardTitle class="text-sm">
					Refused rows
					{#if data.rejects.rejected}
						<Badge variant="destructive" class="ml-2">{data.rejects.rejected}</Badge>
					{/if}
				</CardTitle>
			</CardHeader>
			<CardContent>
				{#if data.rejects.reasons.length === 0}
					<p class="text-muted-foreground text-sm">Nothing was refused in the latest imports.</p>
				{:else}
					<ul class="space-y-2 text-sm">
						{#each data.rejects.reasons as reason (reason.entity + reason.reason)}
							<li class="flex items-start justify-between gap-4">
								<span>
									<span class="text-muted-foreground font-mono text-xs">{reason.entity}</span>
									<span class="ml-2">{reason.reason}</span>
									{#if reason.tenants > 1}
										<span class="text-muted-foreground ml-2 text-xs"
											>across {reason.tenants} firms</span
										>
									{:else if reason.tenant_name}
										<span class="text-muted-foreground ml-2 text-xs">{reason.tenant_name}</span>
									{/if}
								</span>
								<span class="tabular-nums">{reason.rejected}</span>
							</li>
						{/each}
					</ul>
				{/if}
			</CardContent>
		</Card>
	{/if}
</div>
