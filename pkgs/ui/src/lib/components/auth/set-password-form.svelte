<script lang="ts">
	import { Alert, AlertDescription } from '../ui/alert/index.js';
	import AuthCard from './auth-card.svelte';
	import AuthField from './auth-field.svelte';
	import AuthButton from './auth-button.svelte';
	import PasswordStrength from './password-strength.svelte';

	let {
		token,
		tokenKind = 'reset',
		form,
		heading = 'Set your password',
		subtext = 'Choose a password to activate your account and sign in.',
		cta = 'Set password',
		minLength,
		email
	}: {
		/** The token from the emailed link — carried through on submit. */
		token: string;
		tokenKind?: 'invitation' | 'reset';
		/** Form action result — carries `error` on a failed submit. */
		form?: { error?: string } | null;
		heading?: string;
		subtext?: string;
		cta?: string;
		/** The floor the server enforces. Passed in rather than imported: `@graphene/auth` is kept
		 *  out of the client bundle, so the page hands the number down with the hint it already
		 *  hands down. Absent ⇒ no `minlength`, which is the old behaviour. */
		minLength?: number;
		/** The invitee's own address, when the page knows it — fed to the scorer so a password built
		 *  out of somebody's email reads as weak before the server says so. */
		email?: string;
	} = $props();

	let password = $state('');
	let confirm = $state('');

	/**
	 * The last refusal, CLEARED THE MOMENT EITHER FIELD IS TOUCHED.
	 *
	 * `form.error` describes the submit that has already happened, and the password fields are
	 * never repopulated — so after a failure the boxes are empty and the alert above them is
	 * talking about something the user can no longer see. "Passwords do not match" then sits there
	 * accusing a password they are only part-way through retyping, and the next real refusal (a
	 * weak password, an expired link) looks like the same stale message rather than new
	 * information. Clearing on input is what makes the alert mean "this attempt".
	 *
	 * DERIVED, NOT SEEDED IN AN `$effect` — effects do not run on the server, so an effect-seeded
	 * alert renders empty on the action's own response and only appears after hydration. The
	 * dismissal is stored as the `form` object we dismissed FOR, so the next submit brings a new
	 * object and the alert speaks again with nothing to reset.
	 */
	let dismissed = $state<unknown>(null);
	const refusal = $derived(form && form !== dismissed ? (form.error ?? null) : null);

	/**
	 * THE SUBMIT IS DELIBERATELY NOT GATED. It was, briefly — held until length, strength and the
	 * match all passed — and that is a defensible product decision, but it hides the thing this
	 * page most needs to prove: what the provider says when it refuses. A button that cannot be
	 * pressed means the vendor's own rejection path is never exercised, and every message in
	 * `password-errors.ts` stays inferred from the SDK source rather than seen.
	 *
	 * The notes under the meter still say what is wrong, and the action re-validates everything, so
	 * nothing unsafe reaches WorkOS — the user simply finds out a round trip later.
	 */
</script>

<AuthCard {heading} {subtext}>
	{#if refusal}
		<Alert variant="destructive">
			<AlertDescription>{refusal}</AlertDescription>
		</Alert>
	{/if}

	<form method="POST" class="space-y-3">
		<input type="hidden" name="token" value={token} />
		<input type="hidden" name="token_kind" value={tokenKind} />
		<AuthField
			id="password"
			name="password"
			type="password"
			label="New password"
			autocomplete="new-password"
			minlength={minLength}
			bind:value={password}
			oninput={() => (dismissed = form)}
		/>
		<AuthField
			id="confirm"
			name="confirm"
			type="password"
			label="Confirm password"
			autocomplete="new-password"
			minlength={minLength}
			bind:value={confirm}
			oninput={() => (dismissed = form)}
		/>
		<!-- BELOW BOTH FIELDS, not between them. Sitting between the two it split the pair that has
		     to be read together — the password and its confirmation are one act — and a bar that
		     appears mid-form pushes the second field down as you type. Here it is a footnote on the
		     choice, and nothing moves. -->
		<PasswordStrength {password} userInputs={email ? [email] : []} minLength={minLength ?? 0} />
		<!-- Only the MATCH is stated here; length and strength already have their note under the
		     meter, and repeating them would put the same complaint on the screen twice. -->
		{#if confirm.length > 0 && confirm !== password}
			<p class="text-warning text-xs">Passwords do not match.</p>
		{/if}
		<AuthButton class="mt-3">{cta}</AuthButton>
	</form>
</AuthCard>
