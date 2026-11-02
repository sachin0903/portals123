<script lang="ts">
	import { cn, PageHeader } from '@graphene/ui';
	import { page } from '$app/state';
	import { expoOut } from 'svelte/easing';
	import { prefersReducedMotion } from 'svelte/motion';
	import { fade } from 'svelte/transition';
	import UserIcon from '@lucide/svelte/icons/user-round';
	import ShieldIcon from '@lucide/svelte/icons/shield';
	import BellIcon from '@lucide/svelte/icons/bell';

	let { children } = $props();

	// Lighter than the section-level transition: the heading and sub-nav persist,
	// so only the panel needs a quick crossfade as you move between sub-tabs.
	const panelEnter = $derived(
		prefersReducedMotion.current ? { duration: 0 } : { duration: 180, easing: expoOut }
	);

	const sections = [
		{ label: 'Profile', href: '/account', icon: UserIcon },
		{ label: 'Security', href: '/account/security', icon: ShieldIcon },
		{ label: 'Notifications', href: '/account/notifications', icon: BellIcon }
	];

	const isActive = (href: string) =>
		href === '/account'
			? page.url.pathname === '/account'
			: page.url.pathname === href || page.url.pathname.startsWith(href + '/');
</script>

<div>
	<PageHeader title="Account" description="Manage your personal details and account security." />

	<div class="grid gap-8 md:grid-cols-[13rem_1fr]">
		<nav class="-mx-1 flex gap-1 overflow-x-auto px-1 md:mx-0 md:flex-col md:px-0">
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
			{#key page.url.pathname}
				<div in:fade={panelEnter}>
					{@render children()}
				</div>
			{/key}
		</div>
	</div>
</div>
