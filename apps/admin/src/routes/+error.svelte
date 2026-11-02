<script lang="ts">
	import { page } from '$app/state';
	import { Button, Card, CardContent } from '@graphene/ui';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';

	const message = $derived(page.error?.message ?? 'Something went wrong.');
</script>

<svelte:head><title>{page.status} · Operations</title></svelte:head>

<div class="flex min-h-[60vh] items-center justify-center p-6">
	<Card glass class="w-full max-w-md">
		<CardContent class="flex flex-col items-center gap-3 py-10 text-center">
			<TriangleAlertIcon class="text-muted-foreground size-8" />
			<p class="text-4xl font-semibold tracking-tight tabular-nums">{page.status}</p>
			<p class="text-muted-foreground text-sm">{message}</p>
			<Button href="/" class="mt-2">Back to the dashboard</Button>
		</CardContent>
	</Card>
</div>
