<script lang="ts">
	/**
	 * A password's strength: five segments, and the tier named underneath them.
	 *
	 * NOT A GATE. It reports; the identity provider decides. A password can read Strong here and
	 * still be refused for appearing in a breach corpus, which is a fact about the world that no
	 * local scorer holds — so this never disables a submit button and never blocks anything.
	 *
	 * SEGMENTS, NOT A CONTINUOUS FILL. One segment per zxcvbn point, so a step is a real step and
	 * the top tier fills the row completely. A single sliding bar stopped short of its track at the
	 * best score, which read as "still not good enough" on a password that was as strong as the
	 * scale goes.
	 *
	 * THE TIER SITS BELOW, LEFT. Beside the bar it stole width the segments needed and made a full
	 * bar look unfinished; under it, the bar owns the row and the word is read as its caption.
	 *
	 * THE WORD CARRIES THE MEANING, NOT THE COLOUR. Five words; the tint is an accent on a value
	 * that is already legible without it. Same rule `RiskBadge` follows — colour alone is not
	 * readable by everyone, and a red bar with no word says "bad" without saying how bad.
	 */
	import { cn } from '../../utils.js';
	import {
		loadScorer,
		scoreWith,
		MIN_ACCEPTED_SCORE,
		SEGMENTS,
		type Strength
	} from './password-strength.js';

	let {
		password = '',
		/** The person's own email/name, so the scorer penalises a password built out of them —
		 *  the local half of the provider's `PasswordContainsEmail` rule. */
		userInputs = [],
		/** The score the provider accepts from — WorkOS's **Minimum password complexity**. Defaults
		 *  to 3 ("Safely unguessable"). Overridable because it is per-project configuration and
		 *  WorkOS exposes no API to read it, so this mirrors the dashboard rather than following it. */
		minScore = MIN_ACCEPTED_SCORE,
		/** The length floor the form enforces, noted beside the tier when the password is under it.
		 *  A short password can still score well — `xK9#mQ2pR` is 9 characters and scores 3 — so
		 *  without this the meter says nothing about the rule that will actually refuse it.
		 *  0 ⇒ say nothing about length. */
		minLength = 0,
		/** Told whether the password clears the LOCAL rules — length and score. Never a verdict:
		 *  `true` only means nothing here objects, and WorkOS can still refuse it for a breach or
		 *  for containing the person's email. A form uses it to hold its own submit. */
		onassess,
		class: className
	}: {
		password?: string;
		userInputs?: string[];
		minScore?: number;
		minLength?: number;
		onassess?: (ok: boolean) => void;
		class?: string;
	} = $props();

	type Scorer = Awaited<ReturnType<typeof loadScorer>>;
	let scorer = $state<Scorer | null>(null);

	// Loaded on the first character, not on mount: these are cold-start pages reached from an
	// email, and the wordlists are the bulk of the library.
	$effect(() => {
		if (!password || scorer) return;
		let live = true;
		void loadScorer().then((fn) => {
			if (live) scorer = fn;
		});
		return () => {
			live = false;
		};
	});

	const strength = $derived<Strength | null>(scoreWith(scorer, password, userInputs));
	/**
	 * What is still missing, in the order it is worth fixing.
	 *
	 * Separate from the tier, because they are different questions. The tier says how strong the
	 * password IS; these say what would make it acceptable. A 9-character password can score 3 —
	 * calling it "Weak" would be wrong, it just needs more characters — and a 20-character one can
	 * still be "Very Weak", where more characters is not the answer at all.
	 *
	 * Both can apply at once (`password` is short AND guessable), so this is a list rather than one
	 * reason: naming only one sends somebody off to fix half of it and be refused again.
	 */
	const missing = $derived.by(() => {
		const out: string[] = [];
		if (!password) return out;
		if (strength && strength.score < minScore) out.push('Password needs to be stronger');
		if (password.length < minLength) out.push(`at least ${minLength} characters`);
		return out;
	});

	/** 0…4, so the markup iterates a real array — `{#each { length: n }, i}` is newer syntax than
	 *  the pinned eslint-plugin-svelte can parse, and `as _, i` leaves an unused binding. */
	const STEPS = Array.from({ length: SEGMENTS }, (_, i) => i);

	/**
	 * DERIVED FROM THE THRESHOLD, not hard-coded per tier — so a project set to require 4 gets a
	 * correct bar with no edit here, and the colour can never tell somebody a password is fine on a
	 * step the server refuses. Green at or above the threshold, amber one step short, red below.
	 *
	 * The theme's own status tokens, so a firm re-branding them re-themes this too.
	 */
	// FAILS OPEN. Reported from `missing`, which is empty while the dictionaries are still loading
	// — so a slow connection leaves the button usable and lets the server decide, rather than
	// dead-ending somebody on a page they were invited to by email.
	$effect(() => {
		onassess?.(missing.length === 0);
	});

	const tone = $derived.by(() => {
		if (!strength) return 'bg-muted';
		if (strength.score >= minScore) return 'bg-success';
		return strength.score === minScore - 1 ? 'bg-warning' : 'bg-destructive';
	});
</script>

{#if strength}
	<div class={cn('space-y-1.5', className)}>
		<!-- `progressbar` over the zxcvbn 0–4, because that is the real scale — the segments are how
		     it is drawn, not what is measured. `aria-valuetext` is what a screen reader announces, so
		     it reads the WORD rather than "3 of 4". -->
		<div
			class="flex gap-1"
			role="progressbar"
			aria-valuemin={0}
			aria-valuemax={4}
			aria-valuenow={strength.score}
			aria-valuetext="Password strength: {strength.label}"
		>
			{#each STEPS as i (i)}
				<div
					class={cn(
						'h-1.5 flex-1 rounded-full transition-colors duration-200',
						i < strength.filled ? tone : 'bg-muted'
					)}
				></div>
			{/each}
		</div>
		<!-- The tier, then what would fix it. "Very Weak" on its own is a verdict with no next step;
		     the notes are the next step. -->
		<p class="text-muted-foreground text-xs">
			{strength.label}{#if missing.length}<span class="text-warning">
					&nbsp;— {missing.join(' · ')}</span
				>{/if}
		</p>
	</div>
{/if}
