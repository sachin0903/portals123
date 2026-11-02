<script lang="ts">
	import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@graphene/ui';
	import { page } from '$app/state';

	/**
	 * Two ways to arrive, and they need different sentences: a handoff link that could not be
	 * redeemed, or a departure from a browser that could not close the tab for itself. Leaving
	 * normally never reaches this page at all — the tab goes with the session.
	 */
	const left = $derived(page.url.searchParams.get('left') === '1');
</script>

<svelte:head><title>Session ended</title></svelte:head>

<Card>
	<CardHeader>
		<CardTitle>Session ended</CardTitle>
		<CardDescription>
			{#if left}
				You have left this client’s portal. Open a new session from their record in the platform
				portal if you need to look again.
			{:else}
				This link has already been used, or the session it opened has ended. Open a new one from the
				client’s record in the platform portal.
			{/if}
		</CardDescription>
	</CardHeader>
	<CardContent>
		<p class="text-muted-foreground text-sm">You can close this tab.</p>
	</CardContent>
</Card>
