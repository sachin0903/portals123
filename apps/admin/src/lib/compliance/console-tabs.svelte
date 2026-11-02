<script lang="ts">
	// The AML console's two views: the workbench (the work queue) and the client list. The firm
	// filter travels between them, so "Ashworth's work" and "Ashworth's clients" are one click apart.
	import { listHref, workHref } from '$lib/aml';

	let {
		current,
		work = null,
		tenant = ''
	}: {
		current: 'work' | 'clients';
		/** Clients in the work queue, for the tab's count. Null when the count could not be loaded. */
		work?: number | null;
		tenant?: string;
	} = $props();

	const tabs = $derived([
		{ key: 'work', label: 'Workbench', href: workHref({ tenant }), count: work },
		{
			key: 'clients',
			label: 'Client list',
			href: listHref({ tenant, q: '', sort: '', dir: 'asc', page: 1 }),
			count: null
		}
	]);
</script>

<nav aria-label="AML console" class="border-border mb-5 flex gap-1 border-b">
	{#each tabs as tab (tab.key)}
		{@const active = tab.key === current}
		<a
			href={tab.href}
			aria-current={active ? 'page' : undefined}
			class="-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium
			       {active
				? 'border-primary text-foreground'
				: 'text-muted-foreground hover:text-foreground border-transparent'}"
		>
			{tab.label}
			{#if tab.count !== null}
				<span
					class="rounded-full px-1.5 text-xs tabular-nums
					       {tab.count > 0 ? 'bg-warning-subtle text-warning-strong' : 'bg-muted'}"
				>
					{tab.count}
				</span>
			{/if}
		</a>
	{/each}
</nav>
