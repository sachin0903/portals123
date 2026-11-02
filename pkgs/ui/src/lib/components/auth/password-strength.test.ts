/**
 * The strength scale, and the real library behind it.
 *
 * Half of this is pure mapping (score → tier → bar), and half drives the ACTUAL zxcvbn-ts through
 * `loadScorer` — because the mapping being right is worthless if the thing feeding it is wired up
 * wrong. v4 replaced the v3 `zxcvbn`/`zxcvbnOptions` module functions with a `ZxcvbnFactory` class
 * and the language packs export named rather than default, so a stub would have happily proved a
 * scorer that could never load.
 */

import { describe, expect, it } from 'vitest';
import {
	loadScorer,
	scoreWith,
	MIN_ACCEPTED_SCORE,
	SEGMENTS,
	toStrength
} from './password-strength';

describe('the policy we are mirroring', () => {
	/**
	 * WORKOS EXPOSES NO API FOR THIS. There is no password-policy endpoint and no SDK surface —
	 * `/user_management/password_policy`, `/environment`, `/environments/current` and
	 * `/user_management/settings` all 404 against the live API, and the Node SDK names nothing but
	 * authenticate/reset/update. So the dashboard is the source and this is a hand-kept mirror.
	 *
	 * That makes drift silent by nature, which is what this test is for: it states the dashboard
	 * value so changing ours is a deliberate edit with the reason in the diff, not a number that
	 * quietly stops matching. Dashboard → Authentication → Email + Password → Password policy →
	 * **Minimum password complexity**.
	 */
	it('accepts from score 3, matching "Safely unguessable" in the dashboard', () => {
		expect(MIN_ACCEPTED_SCORE).toBe(3);
	});

	it('puts Good on the threshold, so green starts where passing starts', () => {
		// The bar's colour is `score >= minScore`, computed in the component from these two — not a
		// tone hard-coded per tier. A project set to require 4 therefore needs no edit here.
		expect(toStrength({ score: MIN_ACCEPTED_SCORE }).label).toBe('Good');
		expect(toStrength({ score: MIN_ACCEPTED_SCORE - 1 }).label).toBe('Weak');
	});
});

describe('the four tiers', () => {
	it('gives each of the four rungs its own word', () => {
		// 0 and 1 share the bottom — the one place five scores fold onto the dashboard's four rungs,
		// and they share a FILL as well, so no step of the bar means two different things.
		expect(toStrength({ score: 0 }).label).toBe('Very Weak');
		expect(toStrength({ score: 1 }).label).toBe('Very Weak');
		expect(toStrength({ score: 2 }).label).toBe('Weak');
		expect(toStrength({ score: 3 }).label).toBe('Good');
		expect(toStrength({ score: 4 }).label).toBe('Strong');
	});

	it('lights the score on WorkOS’s own 1–4 scale, and fills the row at the top', () => {
		// FOUR segments, not five: the dashboard's complexity picker is 1·2·3·4 and cannot require
		// 0, so the segment a password must clear is the number an administrator actually sets.
		// Scores 0 and 1 therefore share one lit segment and are told apart by their words.
		const lit = [0, 1, 2, 3, 4].map((score) => toStrength({ score: score as 0 }).filled);
		expect(lit).toEqual([1, 1, 2, 3, 4]);
		// Word and fill are the same fact: Very Weak is one segment, Strong is four.
		expect(toStrength({ score: 0 }).filled).toBe(1);
		expect(toStrength({ score: 1 }).filled).toBe(1);
		expect(lit.at(-1)).toBe(SEGMENTS);
	});

	it('lines the lit count up with the threshold an administrator sets', () => {
		// The whole point of matching the scale: at the configured minimum, exactly that many
		// segments are lit. "Needs 3" and "three segments" are the same statement.
		expect(toStrength({ score: MIN_ACCEPTED_SCORE }).filled).toBe(MIN_ACCEPTED_SCORE);
	});

	it('clamps a score off the scale instead of rendering nothing', () => {
		expect(toStrength({ score: 9 as 4 }).label).toBe('Strong');
		expect(toStrength({ score: -1 as 0 }).label).toBe('Very Weak');
		expect(toStrength({ score: NaN as unknown as 0 }).label).toBe('Very Weak');
	});

	it('says nothing at all with no scorer or no password', () => {
		// The row is absent rather than showing an empty bar: a meter rendering at zero before a
		// character is typed reads as a judgement on nothing.
		expect(scoreWith(null, 'anything')).toBeNull();
		expect(scoreWith(() => ({ score: 4 }), '')).toBeNull();
	});
});

describe('the real zxcvbn-ts, loaded the way the component loads it', () => {
	it('loads and scores, which is what proves the v4 wiring', async () => {
		const scorer = await loadScorer();
		expect(scoreWith(scorer, 'p')?.label).toBe('Very Weak');
		expect(scoreWith(scorer, 'correct horse battery staple')?.label).toBe('Strong');
	});

	it('catches a keyboard run, which needs the graphs to be wired', async () => {
		// `graphs` is opt-in since v3. Without it the core scores `qwertyuiop` as though it were
		// random — the silent failure this assertion exists to catch.
		const scorer = await loadScorer();
		const run = scoreWith(scorer, 'qwertyuiop');
		expect(run?.score).toBeLessThanOrEqual(1);
	});

	it('catches a dictionary word, which needs the wordlists to be wired', async () => {
		const scorer = await loadScorer();
		expect(scoreWith(scorer, 'password')?.score).toBe(0);
	});

	it('penalises a password built out of the person’s own email', async () => {
		// The local half of the provider's `PasswordContainsEmail` rule — the bar can say so before
		// the server ever sees it.
		const scorer = await loadScorer();
		const email = 'theo.caldwell@example.com';
		const withEmail = scoreWith(scorer, 'theo.caldwell1', [email])?.score ?? 9;
		const without = scoreWith(scorer, 'theo.caldwell1', [])?.score ?? 0;
		expect(withEmail).toBeLessThanOrEqual(without);
	});

	it('returns the same instance on a second call rather than re-reading the dictionaries', async () => {
		expect(await loadScorer()).toBe(await loadScorer());
	});
});
