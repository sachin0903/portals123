/**
 * How strong a password looks, as a tier a person can read.
 *
 * INFORMATIVE ONLY. This never decides anything: the identity provider does, and it refuses for
 * reasons no local scorer can see — a password in a breach corpus reads as Strong here and is still
 * rejected, because "has this exact string leaked" is a question about the world, not about the
 * characters. So nothing gates a submit on this value, and the server's answer is the answer.
 *
 * WHY zxcvbn-ts SPECIFICALLY, and not a regex of our own: AuthKit scores with zxcvbn-ts, so using
 * the same library is what makes the bar and the verdict mostly agree. A home-grown "one upper, one
 * digit, one symbol" meter would cheerfully call `Passw0rd!` strong and then be contradicted by the
 * server, which is worse than no meter — it teaches people to distrust the thing that is trying to
 * help them.
 *
 * THE DICTIONARIES ARE LAZY. The wordlists are the bulk of the library and this renders on
 * set-password and change-password, both of which are cold-start pages reached from an email. They
 * load on the first keystroke, not on page load, and until they arrive `score()` answers `null`
 * rather than a wrong number — an empty bar is honest where a bar that jumps from Strong to Weak
 * once the dictionaries land is not.
 */

import type { ZxcvbnResult } from '@zxcvbn-ts/core';

/** The four tiers, in the words the bar prints. */
export type StrengthTier = 'very-weak' | 'weak' | 'good' | 'strong';

export type Strength = {
	/** zxcvbn's own 0–4. Kept so a caller can reason about the provider's own threshold. */
	score: 0 | 1 | 2 | 3 | 4;
	tier: StrengthTier;
	label: string;
	/** How many of the four segments are lit, 1–4 — the score on WorkOS's own 1–4 scale. Floors at
	 *  1: score 0 is below that scale, and an empty bar reads as "nothing measured yet". */
	filled: number;
};

/**
 * How many segments the bar draws — FOUR, because that is the scale WorkOS presents.
 *
 * zxcvbn produces five scores (0–4), but the dashboard's **Minimum password complexity** picker
 * offers 1 · 2 · 3 · 4: it cannot require 0, so 0 is not a rung on the policy's ladder but the
 * ground beneath it. Drawing five segments would show a scale nobody administering this can set,
 * and the segment a password had to clear would never line up with the number in the dashboard.
 *
 * The consequence is deliberate: scores 0 and 1 both light ONE segment (`filled` floors at 1) and
 * share the bottom word too, so no step of the bar ever means two different things. A password at
 * the very bottom has still been measured, so it gets a lit segment rather than an empty bar that
 * reads as "nothing yet".
 */
export const SEGMENTS = 4;

/**
 * The score the identity provider accepts from, mirroring **Minimum password complexity** in the
 * WorkOS dashboard (Email + Password → Password policy). 3 = "Safely unguessable", which is what
 * this deployment is set to and what AuthKit recommends.
 *
 * A DEFAULT, NOT A TRUTH. It is per-project configuration and nothing here can read it, so a portal
 * whose project differs passes its own `minScore`. Keeping it as a named constant is the point: the
 * number the bar warns against is stated once and can be checked against the dashboard, rather than
 * being implied by which tier happens to be amber.
 */
export const MIN_ACCEPTED_SCORE = 3;

/**
 * Four words over the four rungs the dashboard shows, so a word and a fill level are the same fact:
 * Very Weak is one segment, Weak two, Good three, Strong four. Nothing to translate between what
 * the bar looks like and what it says.
 *
 * SCORES 0 AND 1 SHARE THE BOTTOM, which is the one place five scores must fold onto four rungs.
 * That is the right place for it: WorkOS cannot require a score of 0, so 0 and 1 are equally "not
 * on the ladder" as far as the policy is concerned, and they share a fill as well as a word — no
 * step of the bar ever means two different things.
 *
 * Bitwarden's mapping, which is where the design came from.
 */
const TIERS: Record<0 | 1 | 2 | 3 | 4, { tier: StrengthTier; label: string }> = {
	0: { tier: 'very-weak', label: 'Very Weak' },
	1: { tier: 'very-weak', label: 'Very Weak' },
	2: { tier: 'weak', label: 'Weak' },
	3: { tier: 'good', label: 'Good' },
	4: { tier: 'strong', label: 'Strong' }
};

/**
 * `score` is widened to a plain `number` rather than the library's `Score` union on purpose: this
 * function's whole job on the way in is to CLAMP, so demanding a value already known to be 0–4
 * would make that defence unreachable and force every caller to cast. The narrow type is what we
 * return, not what we require.
 */
export function toStrength(result: { score: number }): Strength {
	// Clamped rather than trusted: `score` is the library's, and a value outside 0–4 should degrade
	// to a tier rather than index off the end of the map and render an empty bar.
	const raw = Number(result.score);
	const score = (Number.isFinite(raw) ? Math.min(4, Math.max(0, Math.round(raw))) : 0) as
		0 | 1 | 2 | 3 | 4;
	// The score IS the position on the dashboard's 1–4 scale, floored so 0 still shows.
	return { score, ...TIERS[score], filled: Math.max(score, 1) };
}

type Scorer = (password: string, userInputs?: string[]) => Pick<ZxcvbnResult, 'score'>;

let scorer: Scorer | null = null;
let loading: Promise<Scorer> | null = null;

/**
 * Load the library and its English dictionaries once, on first use.
 *
 * `ZxcvbnFactory`, not the bare `zxcvbn()` function: v4 replaced the module-level
 * `zxcvbn`/`zxcvbnOptions` pair with an instance you construct with its options, and the language
 * packs export `dictionary`/`adjacencyGraphs`/`translations` as NAMED exports rather than a
 * default. Both were checked against the installed `.d.ts`, not assumed from the v3 docs.
 *
 * Deliberately NOT `checkAsync`: the async path exists for matchers that call out to a network (the
 * pwned check), and this must never send a half-typed password anywhere. The breach question is the
 * server's to ask, at submit, over its own connection.
 */
export async function loadScorer(): Promise<Scorer> {
	if (scorer) return scorer;
	if (!loading) {
		loading = (async () => {
			const [{ ZxcvbnFactory }, common, en] = await Promise.all([
				import('@zxcvbn-ts/core'),
				import('@zxcvbn-ts/language-common'),
				import('@zxcvbn-ts/language-en')
			]);
			const instance = new ZxcvbnFactory({
				// Keyboard layouts are opt-in and are what catch `qwertyuiop` and `1qaz2wsx`. Without
				// them the core scores a keyboard run as if it were random.
				graphs: common.adjacencyGraphs,
				dictionary: { ...common.dictionary, ...en.dictionary },
				translations: en.translations
			});
			scorer = (password, userInputs = []) => instance.check(password, userInputs);
			return scorer;
		})();
	}
	return loading;
}

/**
 * Score a password, or `null` while the dictionaries are still loading.
 *
 * `userInputs` is what the scorer penalises proximity to — the person's own email and name. It is
 * the local half of the provider's `PasswordContainsEmail` rule, and it is why the bar can say
 * "Very Weak" for a password built out of somebody's own address before the server ever sees it.
 */
export function scoreWith(
	fn: Scorer | null,
	password: string,
	userInputs: string[] = []
): Strength | null {
	if (!fn || !password) return null;
	return toStrength(fn(password, userInputs.filter(Boolean)));
}
