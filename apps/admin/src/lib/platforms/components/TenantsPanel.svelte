<script lang="ts">
	// Which firms are connected to this platform (integrations.tenant_providers), with the
	// sync/fan-out controls. Lives on the platform Overview — "who is connected" is a headline
	// fact about a platform, not a separate area, so this replaced the old Tenants nav item.
	//
	// Enablement CONFIG (which nodes/DSR teams/Microsoft tenant a firm's slice is filtered on)
	// stays on the firm's own tenant page: it needs that firm's pickers, and it is a property of
	// the firm's relationship with the platform rather than of the platform.
	import { enhance } from '$app/forms';
	import { refreshMessage } from '$lib/platforms/registry';
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		ConfirmDialog,
		Money,
		RecordId,
		Sheet
	} from '@graphene/ui';
	import PlayIcon from '@lucide/svelte/icons/play';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Share2Icon from '@lucide/svelte/icons/share-2';
	import WrenchIcon from '@lucide/svelte/icons/wrench';
	import type { PlatformStore } from '../store.svelte';

	interface FirmsForm {
		firmsError?: string;
		syncStarted?: { enqueued: number; models: string[] };
		rebuildStarted?: Record<string, unknown>;
		// The daemon's answer to a per-tenant refresh. `selected` is the only evidence the run had
		// anything to do, and `restart_required` is why it might not have (see below).
		fanout?: { selected: number; restart_required: boolean };
	}

	let {
		store,
		form
		/** Only Morgan Lloyd has sources to register by hand (a Microsoft tenant GUID). */
	}: { store: PlatformStore; form: FirmsForm | null } = $props();

	const firms = $derived(
		store.data.firms ?? { provider: store.data.meta.provider, sources: [], enablements: [] }
	);
	const candidates = $derived(store.data.candidates ?? []);
	/** The route segment, for the per-firm console link. */
	const slug = $derived(store.data.meta.provider);
	let addOpen = $state(false);

	let rebuildOpen = $state(false);
	let rebuildForm = $state<HTMLFormElement>();
	// Close the drawer once the action comes back successful.
	$effect(() => {
		if (form?.rebuildStarted) rebuildOpen = false;
	});

	// The slice values a firm is filtered on, whatever key names them. Rendered as the ids
	// themselves since 0133: `platform.providers` carried a human NAME for each registered
	// Microsoft tenant, and folding it into the firm's own enablement means there is no second
	// row to hold one — the firm's name is on the row already, which is the label that was
	// actually doing the work.
	const sliceIds = (config: Record<string, unknown>): string[] =>
		Object.values(config).flatMap((v) => (Array.isArray(v) ? v.map(String) : [String(v)]));
</script>

<div class="space-y-4">
	{#if form?.firmsError}
		<Alert variant="destructive">
			<AlertDescription>{form.firmsError}</AlertDescription>
		</Alert>
	{/if}
	{#if form?.syncStarted}
		<Alert>
			<AlertDescription>
				Sync queued for {form.syncStarted.models.length} data sets. Check Runs for progress.
			</AlertDescription>
		</Alert>
	{/if}
	{#if form?.rebuildStarted}
		<Alert>
			<AlertDescription>
				Rebuild started. Provider data will be unavailable until it finishes. Check Runs for
				progress.
			</AlertDescription>
		</Alert>
	{/if}
	{#if form?.fanout}
		<!-- `restart_required` means the run selected nothing. A tag that matches no models is a
			 503 carrying the daemon's detail, not this flag. -->
		<Alert variant={refreshMessage(form.fanout).tone}>
			<AlertDescription>{refreshMessage(form.fanout).text}</AlertDescription>
		</Alert>
	{/if}

	<Card>
		<CardHeader class="gap-1">
			<div class="flex items-start justify-between gap-2">
				<div>
					<CardTitle class="text-base">Tenants</CardTitle>
					<CardDescription>
						Firms enabled for this provider and the data available to each one.
					</CardDescription>
				</div>
				<div class="flex shrink-0 gap-2">
					<!-- NO "Register Morgan Lloyd Tenant" BUTTON (0133). Its enablement is keyed on a
					     Microsoft (Dynamics) tenant GUID, which used to be registered and named in a
					     second table before a firm could be pointed at it — two steps, and the write
					     for the first is where the slug bug lived. Registering and enabling are one
					     act now: the firm's own Platforms card takes the GUID and the vendor's
					     reference values together. -->
					<!-- The drawer this opens was UNREACHABLE: `addOpen` was declared and bound, the
					     `candidates` payload was fetched, mapped, typed and rendered — and nothing
					     ever set the flag true. Kept and wired rather than deleted, because "which
					     firms are not connected to this custodian yet" is the question this panel is
					     otherwise silent on. -->
					<Button size="sm" variant="outline" onclick={() => (addOpen = true)}>
						<PlusIcon class="size-4" /> Connect a firm
						{#if candidates.length}
							<span class="text-muted-foreground ml-1 tabular-nums">{candidates.length}</span>
						{/if}
					</Button>
					<form method="POST" action="?/runSync" use:enhance>
						<Button type="submit" size="sm" variant="outline">
							<PlayIcon class="size-4" /> Sync
						</Button>
					</form>
					<form method="POST" action="?/runFanout" use:enhance>
						<Button type="submit" size="sm" variant="outline">
							<Share2Icon class="size-4" /> Refresh all
						</Button>
					</form>
					<!-- Behind a confirm, and NOT next to Sync without one: it resets Interlace-owned
						     state before rebuilding, so the warehouse is empty until the run
						     finishes. Sync is the everyday button; this is the one for a state db that
						     no longer matches its catalogue. -->
					<Button
						size="sm"
						variant="outline"
						onclick={() => (rebuildOpen = true)}
						title="Reset Interlace-owned state and re-run every model"
					>
						<WrenchIcon class="size-4" /> Rebuild
					</Button>
				</div>
			</div>
		</CardHeader>
		<CardContent>
			{#if firms.enablements.length === 0}
				<div class="text-muted-foreground rounded-xl border py-12 text-center text-sm">
					No firms use this platform yet. Select <strong>Connect a firm</strong> to add one.
				</div>
			{:else}
				<div class="divide-y">
					{#each firms.enablements as firm (firm.tenant_id)}
						<div class="flex flex-wrap items-center gap-3 py-3">
							<div class="min-w-48">
								<a href="/tenants/{firm.uid}" class="font-medium hover:underline">
									{firm.name}
								</a>
								<RecordId value={firm.alias} />
							</div>
							<!-- Configuration: the upstream ids this firm's slice covers, by NAME where the
							     warehouse knows it — a bare node id tells an operator nothing. -->
							<div class="flex min-w-0 flex-1 flex-wrap gap-1">
								{#each firm.slices ?? [] as slice (slice.id)}
									<Badge variant="ghost" class="font-normal">
										{slice.name ?? slice.id}
										{#if slice.name}
											<span class="text-muted-foreground ml-1 font-mono text-[10px]">
												{slice.id}
											</span>
										{/if}
									</Badge>
								{:else}
									{#each sliceIds(firm.config) as id (id)}
										<Badge variant="ghost" class="font-mono text-[11px]">
											{id}
										</Badge>
									{/each}
								{/each}
							</div>
							<!-- Null, not zero: an unpromoted or stale slice reads "—". -->
							<div class="w-16 text-right text-xs">
								<div class="tabular-nums">{firm.clients?.toLocaleString('en-GB') ?? '—'}</div>
								<div class="text-muted-foreground">clients</div>
							</div>
							<div class="w-28 text-right text-xs">
								<div>
									{#if firm.aum == null}
										<span class="text-muted-foreground">—</span>
									{:else}
										<Money amount={firm.aum} />
									{/if}
								</div>
								<div class="text-muted-foreground">assets</div>
							</div>
							<div class="text-muted-foreground text-xs">
								{#if firm.enabled}
									last refreshed: {firm.last_fanout_at
										? new Date(firm.last_fanout_at).toLocaleString('en-GB')
										: 'never'}
								{/if}
							</div>
							<Badge variant={firm.enabled ? 'default' : 'secondary'}>
								{firm.enabled ? 'enabled' : 'disabled'}
							</Badge>
							<!-- Into the firm console: what this custodian actually holds for them, which
							     is a different question from the pipeline health shown above. Links to
							     the platform's own firm route, not /tenants/{uid}, which is the firm's
							     Graphene record rather than its slice of this platform. -->
							<Button size="sm" variant="ghost" href="/platforms/{slug}/firms/{firm.uid}">
								Platform data
							</Button>
							{#if firm.enabled}
								<form method="POST" action="?/runFanout" use:enhance>
									<input type="hidden" name="tenant" value={firm.uid} />
									<Button type="submit" size="sm" variant="ghost">Refresh</Button>
								</form>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</CardContent>
	</Card>
</div>

<!-- Add tenant: the CONFIG (which nodes / DSR teams / Microsoft tenant a firm's slice is filtered
     on) needs that firm's own pickers, so this lists the firms not yet connected and hands off to
     each one's platforms page to finish. Better than duplicating the enablement form here and
     having two places that can disagree. -->
<Sheet.Root bind:open={addOpen}>
	<Sheet.Content class="flex w-full flex-col gap-0 p-0">
		<Sheet.Header class="border-b px-6 pt-6 pb-4">
			<Sheet.Title>Add tenant</Sheet.Title>
			<Sheet.Description>
				Choose a firm, then select the provider records it should receive.
			</Sheet.Description>
		</Sheet.Header>
		<div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
			{#if candidates.length === 0}
				<p class="text-muted-foreground py-8 text-center text-sm">
					Every firm is already connected to this platform.
				</p>
			{:else}
				<ul class="divide-border divide-y">
					{#each candidates as tenant (tenant.tenant_id)}
						<li>
							<a
								href="/tenants/{tenant.uid}/settings/platforms"
								class="hover:bg-muted/40 -mx-2 flex items-center gap-3 rounded-md px-2 py-2.5"
							>
								<span class="min-w-0 flex-1">
									<span class="block truncate font-medium">{tenant.name}</span>
									<RecordId value={tenant.alias} />
								</span>
								<ChevronRightIcon class="text-muted-foreground size-4 shrink-0" />
							</a>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	</Sheet.Content>
</Sheet.Root>

<!-- Rebuild is destructive in the window it runs: Interlace-owned state is reset first, so every
     read against this provider's warehouse fails until the re-run lands. Named plainly rather than
     softened — an operator pressing it on a live firm should know what the next few minutes look
     like. The confirm submits the real form action so `use:enhance` and the form result still
     drive the banner above. -->
<form method="POST" action="?/runRebuild" use:enhance bind:this={rebuildForm} class="hidden"></form>
<ConfirmDialog
	bind:open={rebuildOpen}
	destructive
	title="Rebuild this platform's warehouse?"
	description="Rebuilds all provider data from source. Data for this provider will be unavailable until the run finishes. For file-based providers, the full delivery will be read again."
	confirmLabel="Rebuild"
	pendingLabel="Starting…"
	onconfirm={() => rebuildForm?.requestSubmit()}
/>
