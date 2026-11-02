<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Badge,
		Button,
		Card,
		CardAction,
		CardContent,
		CardHeader,
		CardTitle,
		Separator
	} from '@graphene/ui';
	import CircleAlertIcon from '@lucide/svelte/icons/circle-alert';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import CircleDashedIcon from '@lucide/svelte/icons/circle-dashed';
	import RefreshCwIcon from '@lucide/svelte/icons/refresh-cw';
	import type { SectionState, TenantOverview } from './types';

	// The firm's UID, not its serial id: every href below is an admin tenant route, and those are
	// uid-addressed. Named `tenantUid` so a numeric id cannot be passed in by habit.
	let { overview, tenantUid }: { overview: TenantOverview; tenantUid: string } = $props();

	const s = $derived(overview.sections);

	// Three states, never two. "Nothing set up yet" and "set up and failing" both render as
	// not-green, but they are different jobs for different people — so amber and red stay
	// distinct rather than collapsing into one "not ok".
	const ICONS: Record<SectionState, typeof CircleCheckIcon> = {
		ok: CircleCheckIcon,
		pending: CircleDashedIcon,
		attention: CircleAlertIcon
	} as const;
	const TONE: Record<SectionState, string> = {
		ok: 'text-success-strong',
		pending: 'text-muted-foreground',
		attention: 'text-warning-strong'
	};

	/** Bytes as a human size. Binary units, because that is what object stores report. */
	function size(bytes: number): string {
		if (bytes === 0) return '0 B';
		const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
		const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
		const value = bytes / 1024 ** power;
		return `${value.toFixed(value < 10 && power > 0 ? 1 : 0)} ${units[power]}`;
	}

	// The NAME travels with the enablement row (`providers.list_for_tenant`), so there is no table
	// here. There used to be one — nobody outside the database calls it `morgan_lloyd`, and
	// title-casing the key gives "Sei" — and it was one of five such copies across this app.
	const providerName = (p: { provider: string; name?: string }): string =>
		p.name ??
		p.provider
			.split(/[_-]/)
			.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
			.join(' ');

	function when(iso: string): string {
		return new Date(iso).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
	}
</script>

{#snippet light(state: SectionState)}
	{@const Icon = ICONS[state]}
	<Icon class="size-4 shrink-0 {TONE[state]}" aria-label={state} />
{/snippet}

<div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
	<!-- Platforms -->
	<Card glass class="gap-2">
		<CardHeader class="gap-2">
			<CardTitle class="text-sm">Platforms</CardTitle>
			<CardAction>{@render light(s.platforms.state)}</CardAction>
		</CardHeader>
		<CardContent class="space-y-2">
			<div class="text-2xl font-semibold tabular-nums">{s.platforms.connected}</div>
			<p class="text-muted-foreground text-xs">
				connected{s.platforms.configured > s.platforms.connected
					? ` · ${s.platforms.configured - s.platforms.connected} disabled`
					: ''}
			</p>
			{#if s.platforms.providers.length}
				<Separator />
				<ul class="space-y-1 text-xs">
					{#each s.platforms.providers as p (p.provider)}
						<li class="flex items-center justify-between gap-2">
							<span class={p.enabled ? '' : 'text-muted-foreground line-through'}>
								{providerName(p)}
							</span>
							<!-- Enabled and never fanned out is the failure that hides: the connection
							     looks configured and no data has ever moved. -->
							{#if !p.enabled}
								<span class="text-muted-foreground">off</span>
							{:else if p.last_fanout_at}
								<span class="text-muted-foreground tabular-nums">
									{new Date(p.last_fanout_at).toLocaleDateString('en-GB')}
								</span>
							{:else}
								<Badge variant="outline" class="font-normal">never synced</Badge>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		</CardContent>
	</Card>

	<!-- Storage -->
	<Card glass class="gap-2">
		<CardHeader class="gap-2">
			<CardTitle class="text-sm">Storage</CardTitle>
			<CardAction>{@render light(s.storage.state)}</CardAction>
		</CardHeader>
		<CardContent class="space-y-2">
			{#if !s.storage.bucket}
				<p class="text-muted-foreground text-xs">No bucket provisioned.</p>
			{:else if !s.storage.usage}
				<!-- Null figures are NOT zero: nobody has scanned this bucket yet. Rendering 0 B
				     would assert an empty bucket we have not looked in. -->
				<p class="text-muted-foreground text-xs">Not measured yet.</p>
			{:else if s.storage.usage.error}
				<p class="text-warning-strong text-xs break-words">
					{s.storage.usage.error}
				</p>
			{:else}
				{@const u = s.storage.usage}
				<div class="text-2xl font-semibold">{size(u.datalake_bytes + u.files_bytes)}</div>
				<dl class="space-y-1 text-xs">
					<div class="flex justify-between gap-2">
						<dt class="text-muted-foreground">Datalake</dt>
						<dd class="tabular-nums">
							{size(u.datalake_bytes)} · {u.datalake_objects.toLocaleString('en-GB')}
						</dd>
					</div>
					<div class="flex justify-between gap-2">
						<dt class="text-muted-foreground">Files</dt>
						<dd class="tabular-nums">
							{size(u.files_bytes)} · {u.files_objects.toLocaleString('en-GB')}
						</dd>
					</div>
				</dl>
				{#if u.objects_truncated}
					<!-- A capped scan is a lower bound and must say so rather than look authoritative. -->
					<p class="text-warning-strong text-xs">
						The scan reached its limit. Totals are a minimum.
					</p>
				{/if}
			{/if}
			<Separator />
			<div class="flex items-center justify-between gap-2">
				<span class="text-muted-foreground text-xs">
					{s.storage.usage ? `as at ${when(s.storage.usage.computed_at)}` : 'never scanned'}
				</span>
				<form method="POST" action="?/refreshUsage" use:enhance>
					<Button type="submit" size="sm" variant="ghost" title="Rescan the bucket">
						<RefreshCwIcon class="size-3.5" />
					</Button>
				</form>
			</div>
			<div class="flex items-center justify-between gap-2">
				<a
					href="/tenants/{tenantUid}/settings/storage"
					class="text-muted-foreground hover:text-foreground text-xs underline"
				>
					Browse files
				</a>
				<!-- Every provisioning step is idempotent, so this is a repair button rather than a
				     dangerous one — it is what you press when the figures above say "no bucket". -->
				<form method="POST" action="?/provisionStorage" use:enhance>
					<Button type="submit" size="sm" variant="ghost" class="h-7 text-xs">Re-provision</Button>
				</form>
			</div>
		</CardContent>
	</Card>

	<!-- WorkOS -->
	<Card glass class="gap-2">
		<CardHeader class="gap-2">
			<CardTitle class="text-sm">WorkOS</CardTitle>
			<CardAction>{@render light(s.workos.state)}</CardAction>
		</CardHeader>
		<CardContent class="space-y-2">
			<div class="text-2xl font-semibold tabular-nums">{s.workos.members}</div>
			<p class="text-muted-foreground text-xs">
				members · {s.workos.roles} role{s.workos.roles === 1 ? '' : 's'}
			</p>
			<Separator />
			<dl class="space-y-1 text-xs">
				<div class="flex justify-between gap-2">
					<dt class="text-muted-foreground">Staff / client access</dt>
					<!-- These can sum to more than `members`: a person may hold both portals. -->
					<dd class="tabular-nums">{s.workos.staff_members} / {s.workos.client_members}</dd>
				</div>
				{#if s.workos.pending}
					<div class="flex justify-between gap-2">
						<dt class="text-muted-foreground">Awaiting password</dt>
						<dd class="tabular-nums">{s.workos.pending}</dd>
					</div>
				{/if}
			</dl>
			{#if !s.workos.project_id}
				<!-- No project means nobody can log in, however many members are mirrored. -->
				<p class="text-muted-foreground text-xs">No WorkOS project configured.</p>
			{/if}
			<a
				href="/tenants/{tenantUid}/members"
				class="text-muted-foreground hover:text-foreground text-xs underline"
			>
				Manage people
			</a>
		</CardContent>
	</Card>

	<!-- Branding -->
	<Card glass class="gap-2">
		<CardHeader class="gap-2">
			<CardTitle class="text-sm">Branding</CardTitle>
			<CardAction>{@render light(s.branding.state)}</CardAction>
		</CardHeader>
		<CardContent class="space-y-2">
			<div class="text-2xl font-semibold">
				{s.branding.configured ? 'Custom' : 'Default'}
			</div>
			<p class="text-muted-foreground text-xs">
				{s.branding.configured
					? `${s.branding.tokens} setting${s.branding.tokens === 1 ? '' : 's'} set`
					: 'Tracking the platform theme'}
			</p>
			<Separator />
			<p class="text-muted-foreground text-xs">
				<!-- No custom pages is a legitimate configured state, not an omission: an
				     un-customised portal tracks the platform defaults on purpose. -->
				{s.branding.custom_pages ? 'Client portal pages composed' : 'Default portal pages'}
			</p>
			<a
				href="/tenants/{tenantUid}/branding"
				class="text-muted-foreground hover:text-foreground text-xs underline"
			>
				Edit branding
			</a>
		</CardContent>
	</Card>
</div>
