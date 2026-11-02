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

	// What the session sweep actually did — never "signed out everywhere" when nothing was revoked.
	function sweepSentence(s: Sweep | undefined): string {
		if (!s) return '';
		if (!s.listed)
			return 'We could not check your other signed-in sessions. Try “Sign out everywhere else” below.';
		const parts = [
			s.revoked === 0
				? 'There were no other signed-in sessions.'
				: `${s.revoked} other session${s.revoked === 1 ? '' : 's'} signed out.`
		];
		if (s.failed > 0)
			parts.push(`${s.failed} could not be signed out. Try “Sign out everywhere else” below.`);
		if (s.sweptCurrent)
			parts.push('This session was signed out as well, so you will need to sign in again.');
		return parts.join(' ');
	}

	// Pending flags for the five forms.
	let enrolling = $state(false);
	let confirming = $state(false);
	let disabling = $state(false);
	let changingPassword = $state(false);
	let signingOut = $state(false);
	const pending = (set: (v: boolean) => void) => () => {
		set(true);
		return async ({ update }: { update: () => Promise<void> }) => {
			set(false);
			await update();
		};
	};

	// Disabling 2FA is destructive: validate the password field, then confirm before submitting.
	let disableOpen = $state(false);
	let disableFormEl = $state<HTMLFormElement | null>(null);

	// Signing other sessions out kicks the user's other devices off — confirm first.
	let signOutOpen = $state(false);
	let signOutFormEl = $state<HTMLFormElement | null>(null);

	async function copySecret() {
		if (!f.secret) return;
		try {
			await navigator.clipboard.writeText(f.secret);
			toast.success('Copied');
		} catch {
			toast.error('Couldn’t copy the key. Select and copy it manually.');
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
					bind:this={disableFormEl}
					use:enhance={pending((v) => (disabling = v))}
					class="flex flex-col gap-4"
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
							if (disableFormEl?.reportValidity()) disableOpen = true;
						}}
					>
						{disabling ? 'Disabling…' : 'Disable 2FA'}
					</Button>
				</form>
				<ConfirmDialog
					bind:open={disableOpen}
					title="Disable two-factor authentication?"
					description="Your account will no longer be protected by an authenticator app."
					confirmLabel="Disable 2FA"
					destructive
					onconfirm={() => disableFormEl?.requestSubmit()}
				/>
			{:else if f.step === 'confirm'}
				<p class="text-muted-foreground mb-3 text-sm">
					Scan this with your authenticator app, then enter the 6-digit code to confirm.
				</p>
				{#if f.qrCode}
					<img src={f.qrCode} alt="MFA QR code" class="mb-2 h-44 w-44 rounded-md border" />
					<p class="text-muted-foreground mb-4 text-xs">
						Or enter this key manually: <code class="font-mono">{f.secret}</code>
						<Button
							type="button"
							variant="ghost"
							size="icon"
							class="size-6 align-middle"
							aria-label="Copy key"
							onclick={copySecret}
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
					use:enhance={pending((v) => (confirming = v))}
					class="flex flex-col gap-4"
				>
					<input type="hidden" name="factorId" value={f.factorId} />
					<div class="flex flex-col gap-1.5">
						<Label for="code">Authentication code</Label>
						<Input
							id="code"
							name="code"
							inputmode="numeric"
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
				use:enhance={pending((v) => (changingPassword = v))}
				class="flex flex-col gap-4"
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
			<CardTitle>Signed-in sessions</CardTitle>
			<CardDescription>
				Sign out everywhere except this browser. Changing your password does this automatically.
			</CardDescription>
		</CardHeader>
		<CardContent class="space-y-4">
			<LiveSessionList sessions={data.liveSessions} listed={data.sessionsListed} canRevoke={true} />
			{#if f.revokedSession}
				<Alert variant="success">
					<CheckIcon class="size-4" />
					<AlertDescription>That session was signed out.</AlertDescription>
				</Alert>
			{/if}
			{#if f.signedOutOthers}
				<Alert variant="success" class="mb-4">
					<CheckIcon class="size-4" />
					<AlertDescription>{sweepSentence(f.sessions)}</AlertDescription>
				</Alert>
			{/if}
			{#if f.sessionsError}
				<Alert variant="destructive" class="mb-4">
					<AlertDescription>{f.sessionsError}</AlertDescription>
				</Alert>
			{/if}
			<form
				method="POST"
				action="?/signOutOthers"
				bind:this={signOutFormEl}
				use:enhance={pending((v) => (signingOut = v))}
			>
				<Button
					type="button"
					variant="outline"
					disabled={signingOut}
					onclick={() => (signOutOpen = true)}
				>
					{signingOut ? 'Signing out…' : 'Sign out everywhere else'}
				</Button>
			</form>
			<ConfirmDialog
				bind:open={signOutOpen}
				title="Sign out of your other sessions?"
				description="Every other browser and device signed in as you will be signed out. This browser stays signed in."
				confirmLabel="Sign out everywhere else"
				destructive
				onconfirm={() => signOutFormEl?.requestSubmit()}
			/>
		</CardContent>
	</Card>
</div>
