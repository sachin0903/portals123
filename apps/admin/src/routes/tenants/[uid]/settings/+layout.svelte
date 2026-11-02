<script lang="ts">
	// One Settings page for everything Graphene configures ABOUT a firm that the firm cannot
	// configure itself: its record, its sign-in (WorkOS), which platforms feed it, the address its
	// mail comes from, and its storage.
	// These were four sidebar rows under three one-item headings; a firm's Configure group read as
	// a second, messier Firm settings. Same shape as the platform's /account layout — a heading,
	// a side sub-nav, the panel — so the sub-pages stay separate routes with their own loaders and
	// actions (and their write gates, see ../write-gates.test.ts), and the sidebar shows one row.
	import { PageHeader, cn } from '@graphene/ui';
	import { page } from '$app/state';
	import { can } from '$lib/permissions';
	import { settingsSections } from '$lib/tenants/settings-sections';

	let { data, children } = $props();

	const base = $derived(`/tenants/${data.tenant.uid}/settings`);
	const sections = $derived(
		settingsSections(base, {
			canConfigure: data.canConfigure,
			canBrowseStorage: can(page.data.permissions ?? [], page.data.role ?? null, 'book:read')
		})
	);

	const isActive = (href: string) =>
		href === base
			? page.url.pathname === base
			: page.url.pathname === href || page.url.pathname.startsWith(href + '/');
</script>

<div class="space-y-6">
	<PageHeader
		eyebrow={data.tenant.name}
		title="Settings"
		description="How Graphene runs this firm: its record, sign-in, platforms, sending and storage."
	/>

	<div class="grid gap-8 md:grid-cols-[13rem_1fr]">
		<nav
			aria-label="Settings sections"
			class="-mx-1 flex gap-1 overflow-x-auto px-1 md:mx-0 md:flex-col md:px-0"
		>
			{#each sections as item (item.href)}
				{@const active = isActive(item.href)}
				<a
					href={item.href}
					aria-current={active ? 'page' : undefined}
					class={cn(
						'flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
						active
							? 'bg-accent text-accent-foreground'
							: 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
					)}
				>
					<item.icon class="size-4" />
					{item.label}
				</a>
			{/each}
		</nav>

		<div class="min-w-0">
			{@render children()}
		</div>
	</div>
</div>
