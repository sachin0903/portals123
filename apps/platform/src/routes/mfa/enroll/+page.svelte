<script lang="ts">
	import { Alert, AlertDescription, AuthButton, AuthCard, AuthField } from '@graphene/ui';

	let { form } = $props();

	type FormState = {
		step?: 'confirm';
		factorId?: string;
		qrCode?: string;
		secret?: string;
		error?: string;
	};
	const f = $derived((form ?? {}) as unknown as FormState);
</script>

<AuthCard
	heading="Set up two-factor authentication"
	subtext="Two-factor authentication is required. Add an authenticator app to finish signing in."
>
	{#if f.error}
		<Alert variant="destructive">
			<AlertDescription>{f.error}</AlertDescription>
		</Alert>
	{/if}

	{#if f.step === 'confirm'}
		<div class="space-y-3">
			{#if f.qrCode}
				<p class="text-muted-foreground text-sm">
					Scan this with your authenticator app, then enter the 6-digit code.
				</p>
				<img src={f.qrCode} alt="MFA QR code" class="h-44 w-44 rounded-md border" />
				<p class="text-muted-foreground text-xs">
					Or enter this key manually: <code class="font-mono">{f.secret}</code>
				</p>
			{:else}
				<p class="text-muted-foreground text-sm">
					Enter the 6-digit code from the authenticator app you just set up.
				</p>
			{/if}
			<form method="POST" action="?/verify" class="space-y-3">
				<input type="hidden" name="factorId" value={f.factorId} />
				<AuthField
					id="code"
					name="code"
					label="Authentication code"
					inputmode="numeric"
					autocomplete="one-time-code"
				/>
				<AuthButton class="mt-3">Verify &amp; continue</AuthButton>
			</form>
		</div>
	{:else}
		<form method="POST" action="?/enroll">
			<AuthButton>Set up authenticator</AuthButton>
		</form>
	{/if}
</AuthCard>
