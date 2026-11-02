<script lang="ts">
	import { Alert, AlertDescription } from '../ui/alert/index.js';
	import AuthCard from './auth-card.svelte';
	import AuthField from './auth-field.svelte';
	import AuthButton from './auth-button.svelte';

	let {
		form
	}: {
		/** Form action result — `sent` after a submit (always shown, never reveals existence). */
		form?: { sent?: boolean; email?: string } | null;
	} = $props();
</script>

<AuthCard heading="Reset your password" subtext="We'll email you a link to set a new password.">
	{#if form?.sent}
		<Alert>
			<AlertDescription>
				If an account exists for that email, we've sent a link to set a new password. Check your
				inbox.
			</AlertDescription>
		</Alert>
	{:else}
		<form method="POST" class="space-y-3">
			<AuthField
				id="email"
				name="email"
				type="email"
				label="Email address"
				autocomplete="email"
				value={form?.email ?? ''}
			/>
			<AuthButton class="mt-3">Send reset link</AuthButton>
		</form>
	{/if}
	<a
		href="/login"
		class="text-muted-foreground hover:text-foreground tap-target block py-1 text-center text-xs"
	>
		Back to sign in
	</a>
</AuthCard>
