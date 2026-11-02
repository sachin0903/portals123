<script lang="ts">
	import { Button, cn } from '@graphene/ui';
	import { page } from '$app/state';
	import { expoOut } from 'svelte/easing';
	import { prefersReducedMotion } from 'svelte/motion';
	import { fade } from 'svelte/transition';
	import UserIcon from '@lucide/svelte/icons/user-round';
	import ShieldIcon from '@lucide/svelte/icons/shield';
	import BellIcon from '@lucide/svelte/icons/bell';
	import LogOutIcon from '@lucide/svelte/icons/log-out';

	let { children } = $props();

	// Lighter than the section-level transition: the heading and sub-nav persist,
	// so only the panel needs a quick crossfade as you move between sub-tabs.
	const panelEnter = $derived(
		prefersReducedMotion.current ? { duration: 0 } : { duration: 180, easing: expoOut }
	);

	const sections = [
		{ label: 'Profile', href: '/profile', icon: UserIcon },
		{ label: 'Notifications', href: '/profile/notifications', icon: BellIcon },
		{ label: 'Security', href: '/profile/security', icon: ShieldIcon }
	];

	const isActive = (href: string) =>
		href === '/profile'
			? page.url.pathname === '/profile'
			: page.url.pathname === href || page.url.pathname.startsWith(href + '/');
</script>

<div class="space-y-6">
	<div>
		<h1 class="text-3xl font-semibold tracking-tight">Profile</h1>
		<p class="text-muted-foreground mt-1">Manage your personal details and account security.</p>
	</div>

	<div class="grid gap-8 md:grid-cols-[13rem_1fr]">
		<div class="flex flex-col gap-2 md:h-full">
			<nav class="-mx-1 flex gap-1 overflow-x-auto px-1 md:mx-0 md:flex-col md:px-0">
				{#each sections as item (item.href)}
					{@const active = isActive(item.href)}
					<a
						href={item.href}
						aria-current={active ? 'page' : undefined}
						class={cn(
							// min-h-11: the nav scrolls sideways on a phone, which would clip an invisible
							// hit-area extension, so each tab is a 44px target in itself.
							'flex min-h-11 shrink-0 items-center gap-2.5 rounded-lg px-3 text-sm font-medium transition-colors',
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

			<!-- Sign-out lives here now (was the avatar dropdown). Sits at the foot of the
			     sidebar on desktop, below the section tabs on mobile. -->
			<form method="POST" action="/logout" class="md:mt-auto md:border-t md:pt-2">
				<Button
					type="submit"
					variant="ghost"
					class="text-destructive hover:bg-destructive/10 hover:text-destructive w-full justify-start gap-2.5 px-3"
				>
					<LogOutIcon class="size-4" />
					Sign out
				</Button>
			</form>
		</div>

		<div class="min-w-0">
			{#key page.url.pathname}
				<div in:fade={panelEnter}>
					{@render children()}
				</div>
			{/key}
		</div>
	</div>
</div>
