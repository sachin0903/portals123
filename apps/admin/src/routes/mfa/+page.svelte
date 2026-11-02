<script lang="ts">
	import { Alert, AlertDescription, AuthButton, AuthCard, AuthField } from '@graphene/ui';

	let { form } = $props();
</script>

<AuthCard
	heading="Two-factor authentication"
	subtext="Enter the 6-digit code from your authenticator app."
>
	{#if form?.error}
		<Alert variant="destructive">
			<AlertDescription>{form.error}</AlertDescription>
		</Alert>
	{/if}
	<form method="POST" class="space-y-3">
		<AuthField
			id="code"
			name="code"
			label="Authentication code"
			inputmode="numeric"
			autocomplete="one-time-code"
		/>
		<AuthButton class="mt-3">Verify</AuthButton>
	</form>
</AuthCard>
