<script lang="ts">
	// The CLIENT's own notification preferences — a SEPARATE component from the staff
	// `NotificationPreferences`, so the client surface speaks plainly. Per NOTIFICATION now (not per
	// category): each row is one thing the client can be told about, grouped under a category
	// section. The API scopes every write to the login's own "self" contact, so a client changing
	// this only ever changes their own settings. Edits are local until Save; the page persists.
	import { untrack } from 'svelte';
	import LockIcon from '@lucide/svelte/icons/lock';
	import { Button } from '../ui/button/index.js';
	import { Card } from '../ui/card/index.js';
	import { DataTable } from '../ui/data-table/index.js';
	import type { DataTableColumn, DataTableGroupKey } from '../ui/data-table/index.js';
	import { Switch } from '../ui/switch/index.js';
	import { categoryMeta, type NotificationPreference } from './describe.js';

	let {
		preferences = [],
		saving = false,
		onSave
	}: {
		preferences?: NotificationPreference[];
		saving?: boolean;
		onSave?: (prefs: NotificationPreference[]) => void;
	} = $props();

	let rows = $state<NotificationPreference[]>(untrack(() => preferences.map((p) => ({ ...p }))));
	$effect(() => {
		rows = preferences.map((p) => ({ ...p }));
	});

	const dirty = $derived(
		rows.some(
			(r, i) =>
				preferences[i] && (r.in_app !== preferences[i].in_app || r.email !== preferences[i].email)
		)
	);
	// No row with live email drops the whole Email column rather than showing it all disabled.
	const emailLive = $derived(rows.some((r) => r.email_available));

	// Client-facing section labels — the staff category names ("Clients") read wrong to a client
	// viewing their own account, so relabel; fall back to the shared meta label.
	const SECTION_LABELS: Record<string, string> = {
		documents: 'Documents',
		clients: 'Your details',
		accounts: 'Your investments',
		messages: 'Messages',
		tasks: 'Tasks'
	};
	const key = (r: NotificationPreference) => r.trigger_key ?? r.category;

	function categoryGroup(r: NotificationPreference): DataTableGroupKey[] {
		const { label, icon } = categoryMeta(r.category);
		// Bands keep the catalogue's own (first-seen) order.
		const order = rows.findIndex((x) => x.category === r.category);
		return [{ key: r.category, label: SECTION_LABELS[r.category] ?? label, icon, order }];
	}

	// Neither channel column hides on a narrow screen: that would make a preference unreachable.
	const allColumns: DataTableColumn<NotificationPreference>[] = [
		{ key: 'label', header: 'Notification', sortable: true, sortDefault: true },
		{ key: 'in_app', header: 'In portal', align: 'center', headerClass: 'w-24' },
		{ key: 'email', header: 'Email', align: 'center', headerClass: 'w-24' }
	];
	const columns = $derived(allColumns.filter((c) => c.key !== 'email' || emailLive));
</script>

<div class="w-full max-w-2xl">
	<p class="text-muted-foreground text-sm">
		Choose how you'd like to hear about what's happening on your account. These settings are yours
		alone — changing them won't affect anyone else.
	</p>

	<Card class="mt-4 p-0">
		<DataTable
			{columns}
			{rows}
			getKey={key}
			label="Your notification preferences"
			groupBy={categoryGroup}
			empty="You have no notification types to choose from yet."
		>
			{#snippet cell(row, column)}
				{#if column.key === 'label'}
					<div class="min-w-0">
						<p class="text-sm font-medium">{row.label}</p>
						{#if row.description}
							<p class="text-muted-foreground text-xs">{row.description}</p>
						{/if}
					</div>
				{:else if row.mandatory}
					<!-- Your firm sends this one regardless, so it shows locked, not as a dead toggle. -->
					<span
						class="text-muted-foreground inline-flex"
						title="Always sent — this one can't be turned off"
					>
						<LockIcon class="size-3.5" aria-hidden="true" /><span class="sr-only">Always on</span>
					</span>
				{:else if column.key === 'in_app'}
					<Switch bind:checked={row.in_app} aria-label={`${row.label} — show in my portal`} />
				{:else}
					<Switch
						bind:checked={row.email}
						disabled={!row.email_available}
						aria-label={`${row.label} — email me`}
					/>
				{/if}
			{/snippet}
		</DataTable>
	</Card>

	<div class="mt-5 flex justify-end">
		<Button disabled={!dirty || saving} onclick={() => onSave?.(rows)}>
			{saving ? 'Saving…' : 'Save preferences'}
		</Button>
	</div>
</div>
