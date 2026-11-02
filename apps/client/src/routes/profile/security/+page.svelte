<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Button,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		ConfirmDialog,
		Input,
		Label,
		LiveSessionList,
		toast
	} from '@graphene/ui';
	import { enhance } from '$app/forms';
	import CheckIcon from '@lucide/svelte/icons/circle-check';
	import CopyIcon from '@lucide/svelte/icons/copy';

	let { data, form } = $props();

	type Sweep = { listed: boolean; revoked: number; failed: number; sweptCurrent: boolean };
	type FormState = {
		step?: 'confirm';
		enrolled?: boolean;
		disabled?: boolean;
		qrCode?: string;
		secret?: string;
		factorId?: string;
		error?: string;
		pwError?: string;
		passwordChanged?: boolean;
		signedOutOthers?: boolean;
		revokedSession?: boolean;
		sessionsError?: string;
		sessions?: Sweep;
	};
	const f = $derived((form ?? {}) as unknown as FormState);

	/** What the session sweep actually did — never "signed out everywhere" when nothing was. */
	function sweepSentence(s: Sweep | undefined): string {
		if (!s) return '';
		if (!s.listed)
			return 'We couldn’t check your other devices. Try “Sign out other devices” below.';
		const parts = [
			s.revoked === 0
				? 'There were no other signed-in devices.'
				: `${s.revoked} other device${s.revoked === 1 ? '' : 's'} signed out.`
		];
		if (s.failed > 0)
			parts.push(`${s.failed} couldn’t be signed out. Try “Sign out other devices” below.`);
		if (s.sweptCurrent) parts.push('You’ll need to sign in again.');
		return parts.join(' ');
	}

	// Per-form pending states (five independent forms on this page).
	let disabling = $state(false);
	let confirming = $state(false);
	let enrolling = $state(false);
	let changingPassword = $state(false);
	let signingOut = $state(false);

	// Disabling 2FA is guarded by a confirm dialog; confirming submits the password form.
	let disableOpen = $state(false);
	let disableForm = $state<HTMLFormElement | null>(null);

	// Signing other sessions out kicks the user's other devices off — confirm first.
	let signOutOpen = $state(false);
	let signOutForm = $state<HTMLFormElement | null>(null);

	/** A use:enhance factory that just tracks a pending flag around the default behaviour. */
	function pending(set: (v: boolean) => void) {
		return () => {
			set(true);
			return async ({ update }: { update: () => Promise<void> }) => {
				set(false);
				await update();
			};
		};
	}

	async function copySecret(): Promise<void> {
		if (!f.secret) return;
		try {
			await navigator.clipboard.writeText(f.secret);
			toast.success('Setup key copied.');
		} catch {
			toast.error('Couldn’t copy the key. Please copy it manually.');
		}
	}
</script>

<div class="space-y-6">
	<Card glass>
		<CardHeader>
			<CardTitle>Two-factor authentication</CardTitle>
			<CardDescription>Protect your account with an authenticator app (TOTP).</CardDescription>
		</CardHeader>
		<CardContent class="max-w-md">
			{#if f.disabled}
				<!-- Deliberately the default (neutral) Alert: turning 2FA off is a confirmation of the
				     user's own action, not a success or a failure state. -->
				<Alert class="mb-4">
					<AlertDescription>Two-factor authentication has been turned off.</AlertDescription>
				</Alert>
			{/if}

			{#if (f.enrolled || data.enrolled) && !f.disabled}
				<Alert variant="success" class="mb-4">
					<CheckIcon class="size-4" />
					<AlertDescription>Two-factor authentication is enabled.</AlertDescription>
				</Alert>
				{#if f.error}
					<Alert variant="destructive" class="mb-4"
						><AlertDescription>{f.error}</AlertDescription></Alert
					>
				{/if}
				<form
					method="POST"
					action="?/disable"
					class="flex flex-col gap-4"
					bind:this={disableForm}
					use:enhance={pending((v) => (disabling = v))}
				>
					<div class="flex flex-col gap-1.5">
						<Label for="password">Confirm your password to disable</Label>
						<Input
							id="password"
							name="password"
							type="password"
							autocomplete="current-password"
							required
						/>
					</div>
					<Button
						type="button"
						variant="destructive"
						class="self-start"
						disabled={disabling}
						onclick={() => {
							if (disableForm?.reportValidity()) disableOpen = true;
						}}
					>
						{disabling ? 'Disabling…' : 'Disable 2FA'}
					</Button>
				</form>
				<ConfirmDialog
					bind:open={disableOpen}
					title="Disable two-factor authentication?"
					description="Your account will no longer ask for a code when you sign in. You can re-enable it at any time."
					confirmLabel="Disable 2FA"
					destructive
					onconfirm={() => disableForm?.requestSubmit()}
				/>
			{:else if f.step === 'confirm'}
				<p class="text-muted-foreground mb-3 text-sm">
					Scan this with your authenticator app, then enter the 6-digit code to confirm.
				</p>
				{#if f.qrCode}
					<img src={f.qrCode} alt="MFA QR code" class="mb-2 h-44 w-44 rounded-md border" />
					<p class="text-muted-foreground mb-4 flex items-center gap-1 text-xs">
						Or enter this key manually: <code class="font-mono">{f.secret}</code>
						<Button
							variant="ghost"
							size="icon-xs"
							onclick={copySecret}
							aria-label="Copy setup key"
							title="Copy setup key"
						>
							<CopyIcon class="size-3.5" />
						</Button>
					</p>
				{/if}
				{#if f.error}
					<Alert variant="destructive" class="mb-4"
						><AlertDescription>{f.error}</AlertDescription></Alert
					>
				{/if}
				<form
					method="POST"
					action="?/confirm"
					class="flex flex-col gap-4"
					use:enhance={pending((v) => (confirming = v))}
				>
					<input type="hidden" name="factorId" value={f.factorId} />
					<div class="flex flex-col gap-1.5">
						<Label for="code">Authentication code</Label>
						<Input
							id="code"
							name="code"
							inputmode="numeric"
							maxlength={6}
							autocomplete="one-time-code"
							placeholder="6-digit code"
							required
						/>
					</div>
					<Button type="submit" class="w-full" disabled={confirming}>
						{confirming ? 'Confirming…' : 'Confirm'}
					</Button>
				</form>
			{:else}
				<p class="text-muted-foreground mb-4 text-sm">
					Add an authenticator app (Google Authenticator, 1Password, Authy) for two-factor sign-in.
				</p>
				{#if f.error}
					<Alert variant="destructive" class="mb-4"
						><AlertDescription>{f.error}</AlertDescription></Alert
					>
				{/if}
				<form method="POST" action="?/enroll" use:enhance={pending((v) => (enrolling = v))}>
					<Button type="submit" disabled={enrolling}>
						{enrolling ? 'Setting up…' : 'Enable 2FA'}
					</Button>
				</form>
			{/if}
		</CardContent>
	</Card>

	<Card glass>
		<CardHeader>
			<CardTitle>Password</CardTitle>
			<CardDescription>Change the password you use to sign in.</CardDescription>
		</CardHeader>
		<CardContent class="max-w-md">
			{#if f.passwordChanged}
				<Alert variant="success" class="mb-4">
					<CheckIcon class="size-4" />
					<AlertDescription>
						Your password has been updated. {sweepSentence(f.sessions)}
					</AlertDescription>
				</Alert>
			{/if}
			{#if f.pwError}
				<Alert variant="destructive" class="mb-4"
					><AlertDescription>{f.pwError}</AlertDescription></Alert
				>
			{/if}
			<form
				method="POST"
				action="?/password"
				class="flex flex-col gap-4"
				use:enhance={pending((v) => (changingPassword = v))}
			>
				<div class="flex flex-col gap-1.5">
					<Label for="current">Current password</Label>
					<Input id="current" name="current" type="password" autocomplete="current-password" />
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="next">New password</Label>
					<Input
						id="next"
						name="next"
						type="password"
						autocomplete="new-password"
						minlength={data.minPasswordLength}
					/>
					<p class="text-muted-foreground text-xs">{data.passwordHint}</p>
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="confirm">Confirm new password</Label>
					<Input
						id="confirm"
						name="confirm"
						type="password"
						autocomplete="new-password"
						minlength={data.minPasswordLength}
					/>
				</div>
				<Button type="submit" variant="outline" class="self-start" disabled={changingPassword}>
					{changingPassword ? 'Updating…' : 'Update password'}
				</Button>
			</form>
		</CardContent>
	</Card>

	<Card glass>
		<CardHeader>
			<CardTitle>Signed-in devices</CardTitle>
			<CardDescription>
				Sign out on every other browser and device. Changing your password does this automatically.
			</CardDescription>
		</CardHeader>
		<CardContent class="space-y-4">
			<LiveSessionList sessions={data.liveSessions} listed={data.sessionsListed} canRevoke={true} />
			{#if f.revokedSession}
				<Alert variant="success">
					<CheckIcon class="size-4" />
					<AlertDescription>That device was signed out.</AlertDescription>
				</Alert>
			{/if}
			{#if f.signedOutOthers}
				<Alert variant="success" class="mb-4">
					<CheckIcon class="size-4" />
					<AlertDescription>{sweepSentence(f.sessions)}</AlertDescription>
				</Alert>
			{/if}
			{#if f.sessionsError}
				<Alert variant="destructive" class="mb-4"
					><AlertDescription>{f.sessionsError}</AlertDescription></Alert
				>
			{/if}
			<form
				method="POST"
				action="?/signOutOthers"
				bind:this={signOutForm}
				use:enhance={pending((v) => (signingOut = v))}
			>
				<Button
					type="button"
					variant="outline"
					disabled={signingOut}
					onclick={() => (signOutOpen = true)}
				>
					{signingOut ? 'Signing out…' : 'Sign out other devices'}
				</Button>
			</form>
			<ConfirmDialog
				bind:open={signOutOpen}
				title="Sign out of your other devices?"
				description="Every other browser and device signed in as you will be signed out. This browser stays signed in."
				confirmLabel="Sign out other devices"
				destructive
				onconfirm={() => signOutForm?.requestSubmit()}
			/>
		</CardContent>
	</Card>
</div>
