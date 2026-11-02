<script lang="ts">
	import { Alert, AlertDescription, AuthButton, AuthCard, AuthField } from '@graphene/ui';
	import { enhance } from '$app/forms';
	import LiquidGlass from '$lib/LiquidGlass.svelte';

	let { form } = $props();

	let submitting = $state(false);
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
	<form
		method="POST"
		class="space-y-3"
		use:enhance={() => {
			submitting = true;
			return async ({ update }) => {
				submitting = false;
				await update();
			};
		}}
	>
		<AuthField
			id="code"
			name="code"
			label="Authentication code"
			inputmode="numeric"
			maxlength={6}
			autocomplete="one-time-code"
		/>
		<!-- AuthButton has no disabled prop (packages/ui); pointer-events guards a double submit. -->
		<AuthButton class="mt-3 {submitting ? 'pointer-events-none opacity-60' : ''}">
			{submitting ? 'Verifying…' : 'Verify'}
		</AuthButton>
	</form>
</AuthCard>

<LiquidGlass target=".glass" />
