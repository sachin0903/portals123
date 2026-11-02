<script lang="ts">
	import { Alert, AlertDescription } from '../ui/alert/index.js';
	import AuthCard from './auth-card.svelte';
	import AuthField from './auth-field.svelte';
	import AuthButton from './auth-button.svelte';

	let {
		form,
		heading = 'Welcome back',
		subtext = 'Sign in to your Graphene portal.',
		footer
	}: {
		/** The form action result — carries `error` / `email` on a failed submit. */
		form?: { error?: string; email?: string } | null;
		heading?: string;
		subtext?: string;
		/** Optional fine print under the form (e.g. how accounts are provisioned). */
		footer?: string;
	} = $props();
</script>

<AuthCard {heading} {subtext}>
	{#if form?.error}
		<Alert variant="destructive">
			<AlertDescription>{form.error}</AlertDescription>
		</Alert>
	{/if}

	<form method="POST" class="space-y-3">
		<AuthField
			id="email"
			name="email"
			type="email"
			label="Email address"
			autocomplete="email"
			value={form?.email ?? ''}
		/>
		<AuthField
			id="password"
			name="password"
			type="password"
			label="Password"
			autocomplete="current-password"
		/>
		<div class="text-right">
			<!-- `tap-target`: a 16px line of text with a 44px hit area; the field above and the
			     button below are their own targets and the extension stops short of both. -->
			<a
				href="/forgot-password"
				class="text-muted-foreground hover:text-foreground tap-target inline-block py-1 text-xs"
			>
				Forgot password?
			</a>
		</div>

		<AuthButton class="mt-3">Sign in</AuthButton>
	</form>

	{#if footer}
		<p class="text-muted-foreground text-center text-xs">{footer}</p>
	{/if}
</AuthCard>
