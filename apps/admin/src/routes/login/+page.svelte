<script lang="ts">
	import { Alert, AlertDescription, LoginForm } from '@graphene/ui';

	let { data, form } = $props();
</script>

<!-- Why they are here (session ended, password just set, email unconfirmed) — resolved
     server-side in +page.server.ts so the copy lives in one place. -->
{#if data.notice && !form?.error}
	<Alert variant={data.notice.tone} class="mb-6">
		<AlertDescription>{data.notice.message}</AlertDescription>
	</Alert>
{/if}

<LoginForm {form} subtext="Internal Operations" footer="Staff access only." />
