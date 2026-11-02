<script lang="ts">
	import { Button, Card, CardContent } from '@graphene/ui';
	import { page } from '$app/state';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';

	const status = $derived(page.status);
	// Status-aware, friendly message — never a raw stack trace or bare "Internal Error".
	const message = $derived.by(() => {
		if (status === 404) return 'We couldn’t find that page. Check the address or return home.';
		if (status === 403) return 'You don’t have access to that page.';
		return 'We couldn’t open this page. Please try again or contact your adviser.';
	});
</script>

<svelte:head><title>Something went wrong</title></svelte:head>

<div class="flex min-h-[60svh] items-center justify-center p-6">
	<Card class="w-full max-w-md">
		<CardContent class="flex flex-col items-center gap-4 py-10 text-center">
			<div class="bg-muted flex size-14 items-center justify-center rounded-full">
				<TriangleAlertIcon class="text-muted-foreground size-7" />
			</div>
			<div>
				<h1 class="text-lg font-semibold">Something went wrong</h1>
				<p class="text-muted-foreground mt-1 text-sm">{message}</p>
			</div>
			<Button href="/">Back home</Button>
		</CardContent>
	</Card>
</div>
