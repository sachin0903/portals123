<script lang="ts">
	import type { Snippet } from 'svelte';
	import Logo from '../brand/logo.svelte';
	import { buttonVariants } from '../ui/button/index.js';

	let {
		portal,
		user = null,
		children
	}: {
		/** Portal label shown beside the brand, e.g. "Client" / "Platform" / "Operations". */
		portal: string;
		/** Signed-in user, or null on the pre-auth pages (login / MFA / verify). */
		user?: { email: string } | null;
		children?: Snippet;
	} = $props();
</script>

{#if user}
	<div class="bg-muted/30 min-h-svh">
		<header class="bg-background border-b">
			<div class="mx-auto flex h-14 max-w-5xl items-center justify-between px-4">
				<a href="/" class="flex items-center gap-2.5" aria-label="Graphene {portal} — home">
					<Logo class="text-foreground" markClass="h-6" />
					<span class="text-muted-foreground border-l pl-2.5 text-sm font-medium">{portal}</span>
				</a>
				<div class="flex items-center gap-3">
					<span class="text-muted-foreground hidden text-sm sm:inline">{user.email}</span>
					<a href="/settings/security" class={buttonVariants({ variant: 'ghost', size: 'sm' })}>
						Security
					</a>
					<form method="POST" action="/logout">
						<button type="submit" class={buttonVariants({ variant: 'outline', size: 'sm' })}>
							Sign out
						</button>
					</form>
				</div>
			</div>
		</header>
		<main class="mx-auto max-w-5xl px-4 py-8">
			{@render children?.()}
		</main>
	</div>
{:else}
	<div class="bg-muted/30 flex min-h-svh items-center justify-center p-4">
		<main class="w-full max-w-sm">
			{@render children?.()}
		</main>
	</div>
{/if}
