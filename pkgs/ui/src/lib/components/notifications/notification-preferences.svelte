<script lang="ts">
	// Per-category channel preferences. Two postures, one component:
	//   • personal (default) — a user's own in-app/email toggles (platform/admin Account pages).
	//   • tenantConfig — a firm's DEFAULTS, split into an ADVISERS section and a CLIENTS section,
	//     each row carrying a firm-wide Enabled off-switch and a Mandatory toggle (which forces the
	//     default even over a recipient's opt-out). Edited by Graphene ops on the admin tenant page
	//     (docs/notifications.md, the tenant layer of resolution).
	// The email toggle is disabled unless the channel is available (email_available). Edits are local
	// until Save, which hands the full set to onSave — the app owns persistence.
	import { untrack } from 'svelte';
	import LockIcon from '@lucide/svelte/icons/lock';
	import { Button } from '../ui/button/index.js';
	import { Card } from '../ui/card/index.js';
	import { DataTable } from '../ui/data-table/index.js';
	import type { DataTableColumn, DataTableGroupKey } from '../ui/data-table/index.js';
	import { Switch } from '../ui/switch/index.js';
	import { default as Segmented } from '../ui/segmented/segmented.svelte';
	import { categoryMeta, toneClasses, type NotificationPreference } from './describe.js';

	let {
		preferences = [],
		saving = false,
		tenantConfig = false,
		onSave
	}: {
		preferences?: NotificationPreference[];
		saving?: boolean;
		/** Firm-config posture: advisers/clients sections with Enabled + Mandatory, not a personal
		 *  on/off. */
		tenantConfig?: boolean;
		onSave?: (prefs: NotificationPreference[]) => void;
	} = $props();

	// A local editable copy; reset whenever the loader passes a fresh set.
	let rows = $state<NotificationPreference[]>(untrack(() => preferences.map((p) => ({ ...p }))));
	$effect(() => {
		rows = preferences.map((p) => ({ ...p }));
	});

	const dirty = $derived(
		rows.some(
			(r, i) =>
				preferences[i] &&
				(r.in_app !== preferences[i].in_app ||
					r.email !== preferences[i].email ||
					r.enabled !== preferences[i].enabled ||
					r.mandatory !== preferences[i].mandatory)
		)
	);

	const key = (r: NotificationPreference) => `${r.category}:${r.trigger_key ?? r.audience ?? ''}`;

	// Firm-config sections, advisers first. Only ONE shows at a time; a switch flips between them.
	const SECTIONS = [
		{ key: 'adviser', label: 'Advisers' },
		{ key: 'client', label: 'Clients' }
	];
	let section = $state('adviser');
	const available = $derived(SECTIONS.filter((s) => rows.some((r) => r.audience === s.key)));
	// Keep the active section valid as the data loads (fall back to the first available).
	$effect(() => {
		const first = available[0];
		if (first && !available.some((s) => s.key === section)) section = first.key;
	});

	const tableRows = $derived(tenantConfig ? rows.filter((r) => r.audience === section) : rows);

	/** One band per category, in the catalogue's first-seen order (personal posture only). */
	function categoryGroup(r: NotificationPreference): DataTableGroupKey[] {
		const { label, icon } = categoryMeta(r.category);
		const order = tableRows.findIndex((x) => x.category === r.category);
		return [{ key: r.category, label, icon, order }];
	}

	const allColumns: DataTableColumn<NotificationPreference>[] = [
		{ key: 'label', header: 'Notification', sortable: true, sortDefault: true },
		{ key: 'enabled', header: 'Enabled', align: 'center', headerClass: 'w-20' },
		{ key: 'in_app', header: 'In-app', align: 'center', headerClass: 'w-20' },
		{ key: 'email', header: 'Email', align: 'center', headerClass: 'w-20' },
		{ key: 'mandatory', header: 'Mandatory', align: 'center', headerClass: 'w-24' }
	];
	const firmOnly = (key: string) => key === 'enabled' || key === 'mandatory';
	const columns = $derived(allColumns.filter((c) => tenantConfig || !firmOnly(c.key)));
</script>

<div class="w-full">
	<div class="mb-1">
		<h2 class="text-base font-semibold">
			{tenantConfig ? 'Notification settings' : 'Notification preferences'}
		</h2>
		<p class="text-muted-foreground text-sm">
			{tenantConfig
				? 'How advisers and clients are notified, and which categories are on firm-wide.'
				: 'Choose which notifications appear in your notification centre.'}
		</p>
		{#if tenantConfig}
			<p class="text-muted-foreground/80 mt-1 text-xs">
				<strong class="font-medium">Enabled</strong> turns a category on or off firm-wide.
				<strong class="font-medium">In-app</strong>/<strong class="font-medium">Email</strong> are
				the channels for people who haven't set their own preference.
				<strong class="font-medium">Mandatory</strong> means the recipient cannot turn it off.
			</p>
		{/if}
	</div>

	{#if tenantConfig && available.length > 1}
		<Segmented
			options={available.map((s) => ({ value: s.key, label: s.label }))}
			bind:value={section}
			class="mt-4 mb-3"
		/>
	{/if}

	<Card class="mt-4 p-0">
		<DataTable
			{columns}
			rows={tableRows}
			getKey={key}
			label={tenantConfig ? 'Firm notification defaults' : 'Notification preferences'}
			groupBy={tenantConfig ? undefined : categoryGroup}
			empty={tenantConfig
				? 'No notification types for this audience yet.'
				: 'No notification types to configure yet.'}
		>
			{#snippet cell(r, column)}
				{@const off = tenantConfig && !r.enabled}
				{#if column.key === 'label'}
					{@const meta = categoryMeta(r.category)}
					{@const Icon = meta.icon}
					<div class="flex items-center gap-3">
						{#if tenantConfig}
							<span
								class={'flex size-8 shrink-0 items-center justify-center rounded-full ' +
									toneClasses(meta.tone)}
							>
								<Icon class="size-4" />
							</span>
						{/if}
						<div class="min-w-0">
							<p class="text-sm font-medium">{r.label}</p>
							<p class="text-muted-foreground truncate text-xs">{r.description}</p>
						</div>
					</div>
				{:else if column.key === 'enabled'}
					<Switch bind:checked={r.enabled} aria-label={`${r.label} enabled firm-wide`} />
				{:else if column.key === 'mandatory'}
					<Switch
						bind:checked={r.mandatory}
						disabled={off}
						aria-label={`${r.label} is mandatory; recipients can’t turn it off`}
					/>
				{:else if !tenantConfig && r.mandatory}
					<!-- The resolver ignores a recipient's toggle on a mandatory one, so it shows locked. -->
					<span
						class="text-muted-foreground inline-flex"
						title="Always sent because your firm has made it mandatory"
					>
						<LockIcon class="size-3.5" aria-hidden="true" /><span class="sr-only">Always on</span>
					</span>
				{:else if column.key === 'in_app'}
					<Switch bind:checked={r.in_app} disabled={off} aria-label={`In-app ${r.label}`} />
				{:else}
					<Switch
						bind:checked={r.email}
						disabled={!r.email_available || off}
						aria-label={`Email ${r.label}`}
					/>
				{/if}
			{/snippet}
		</DataTable>
	</Card>

	<div class="mt-4 flex justify-end">
		<Button disabled={!dirty || saving} onclick={() => onSave?.(rows)}>
			{saving ? 'Saving…' : tenantConfig ? 'Save settings' : 'Save preferences'}
		</Button>
	</div>
</div>
