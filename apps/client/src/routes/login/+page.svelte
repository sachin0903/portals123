<script lang="ts">
	import { Alert, AlertDescription, LoginForm } from '@graphene/ui';
	import LiquidGlass from '$lib/LiquidGlass.svelte';

	let { data, form } = $props();
</script>

<!-- Real WebGL liquid-glass refraction on the login card (the only `.glass` here).
     Progressive enhancement: falls back to the CSS `.glass` material if WebGL or
     the snapshot is unavailable, or under reduced-motion. -->
<LiquidGlass target=".glass" />

<!-- Why they are here (session ended, password just set, email unconfirmed) — resolved
     server-side in +page.server.ts so the copy lives in one place. -->
{#if data.notice && !form?.error}
	<Alert variant={data.notice.tone} class="mb-6">
		<AlertDescription>{data.notice.message}</AlertDescription>
	</Alert>
{/if}

<LoginForm
	{form}
	subtext="Sign in to view your account."
	footer="Contact your adviser if you need help signing in."
/>
