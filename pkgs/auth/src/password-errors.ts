/**
 * What the identity provider said when it refused a password, in words a person can act on.
 *
 * THE DEFECT THIS EXISTS FOR. A weak password is a **422**, and the WorkOS SDK's
 * `UnprocessableEntityException` carries its `code`/`message` **on the instance** — it has no
 * `rawData` at all (only `GenericServerException`, `AuthenticationException` and `OauthException`
 * have one). Both password paths in `auth.ts` read `e.rawData` alone, so `code` and `message` came
 * back empty, no branch matched, and every rejected password on the set-password page fell through
 * to the generic "Could not set your password. Please try again." The `'weak'` outcome — and the
 * carefully-written `WEAK_PASSWORD_MESSAGE` behind it — could not be reached in production. Every
 * test double threw `{ rawData: { code: 'password_too_weak' } }`, a shape the SDK cannot produce
 * for this error, which is why the suite was green throughout.
 *
 * THE SECOND TRAP, AND IT DECIDES THE SHAPE OF THIS MODULE: the two exception classes disagree
 * about `errors[]`. `BadRequestException` (400) keeps it — `if (errors) this.errors = errors` —
 * but `UnprocessableEntityException` (422) NEVER STORES IT. It folds the codes into the message
 * and throws the array away:
 *
 *     this.message = `The following ${requirement} must be met:\n`;
 *     for (const { code } of errors) this.message = this.message.concat(`\t${code}\n`);
 *
 * So on the one status that a rejected password actually uses, the specific reason survives ONLY
 * inside the message string. A classifier reading structured fields alone finds nothing but the
 * summary code and reports every rejection as generically weak. That is why the haystack below is
 * codes AND message text, and why the message is matched but never echoed: "The following
 * requirement must be met: password_too_weak" is not something to show a person.
 *
 * AuthKit names six password failures (it scores with zxcvbn-ts). Two of them — a breached
 * password and one containing the user's email — are things no strength meter can predict, which
 * is exactly why the message still has to do real work even once the page has a strength bar.
 */

import { MIN_PASSWORD_LENGTH, WEAK_PASSWORD_MESSAGE } from './validation';

/**
 * Every place a WorkOS error may carry its code, flattened into one list.
 *
 * `errors[]` FIRST, because it is the most specific: a 422 that carries it is naming the exact
 * rules that failed, where `.code` is the summary. Instance before `rawData` for the reason above.
 */
export function passwordErrorCodes(error: unknown): string[] {
	const e = error as
		| {
				code?: unknown;
				errors?: unknown;
				rawData?: { code?: unknown; errors?: unknown } | null;
		  }
		| null
		| undefined;
	const out: string[] = [];
	const push = (value: unknown) => {
		if (typeof value === 'string' && value) out.push(value.toLowerCase());
	};
	const pushList = (value: unknown) => {
		if (!Array.isArray(value)) return;
		for (const entry of value) {
			if (typeof entry === 'string') push(entry);
			else push((entry as { code?: unknown } | null)?.code);
		}
	};
	pushList(e?.errors);
	pushList(e?.rawData?.errors);
	push(e?.code);
	push(e?.rawData?.code);
	return out;
}

/** The provider's message, from wherever this error kind keeps it. */
export function providerMessage(error: unknown): string {
	const e = error as { message?: unknown; rawData?: { message?: unknown } | null } | null;
	const instance = typeof e?.message === 'string' ? e.message : '';
	const raw = typeof e?.rawData?.message === 'string' ? e.rawData.message : '';
	return instance || raw;
}

/**
 * Too short, said two ways.
 *
 * `TOO_SHORT` quotes OUR floor, which is a hand-kept mirror of the dashboard. `TOO_SHORT_UNKNOWN`
 * is for the case where we have PROOF that mirror is stale (see `passwordRejection`): a number we
 * know to be wrong is worse than no number, because the reader does exactly what it says and is
 * refused again.
 */
const TOO_SHORT =
	`That password is too short — use at least ${MIN_PASSWORD_LENGTH} characters. ` +
	'Three or four unrelated words make a strong, memorable one.';
const TOO_SHORT_UNKNOWN =
	'That password is too short. Try a longer passphrase — three or four unrelated words make a ' +
	'strong, memorable one.';

/** What a rejection is ABOUT, so a caller can reason about it rather than only print it. */
type Rule = 'pwned' | 'contains-email' | 'too-short' | 'too-long' | 'character-type' | 'too-weak';

/**
 * The six AuthKit password failures, in our own words.
 *
 * Matched on a SUBSTRING of the code rather than equality: the exact strings are the vendor's and
 * are not in the SDK (they arrive only in a live response body), so an exact list is a list that
 * goes stale silently. A substring match on the distinguishing word survives `password_too_weak`,
 * `PasswordTooWeak` and `password_strength_error` alike, and the order below is deliberate —
 * "pwned" and "email" are checked before the generic weakness, because a breached password is also
 * a weak one and the specific reason is the useful one.
 */
const REASONS: ReadonlyArray<readonly [needle: string, rule: Rule, message: string]> = [
	[
		'pwned',
		'pwned',
		'That password has appeared in a known data breach, so it is no longer safe to use — ' +
			'even if it looks strong. Pick a different one.'
	],
	[
		'breach',
		'pwned',
		'That password has appeared in a known data breach, so it is no longer safe to use — ' +
			'even if it looks strong. Pick a different one.'
	],
	[
		'containsemail',
		'contains-email',
		'Your password cannot contain your email address. Pick a different one.'
	],
	[
		'contains_email',
		'contains-email',
		'Your password cannot contain your email address. Pick a different one.'
	],
	['tooshort', 'too-short', TOO_SHORT],
	['too_short', 'too-short', TOO_SHORT],
	['toolong', 'too-long', 'That password is too long. Use 256 characters or fewer.'],
	['too_long', 'too-long', 'That password is too long. Use 256 characters or fewer.'],
	[
		'charactertype',
		'character-type',
		'That password needs a wider mix of characters — add upper and lower case, a digit or a ' +
			'symbol.'
	],
	[
		'character_type',
		'character-type',
		'That password needs a wider mix of characters — add upper and lower case, a digit or a ' +
			'symbol.'
	],
	['tooweak', 'too-weak', WEAK_PASSWORD_MESSAGE],
	['too_weak', 'too-weak', WEAK_PASSWORD_MESSAGE],
	['strength', 'too-weak', WEAK_PASSWORD_MESSAGE]
];

/**
 * Was this error the provider refusing the PASSWORD, and if so what should the user read?
 *
 * `null` means "not a password rejection" — the caller must not guess, because the same call can
 * fail for an expired token or an outage and telling somebody their password is weak when the link
 * has expired sends them off changing the one thing that was fine.
 *
 * A recognised-but-unmapped password rejection still answers, with `WEAK_PASSWORD_MESSAGE`: the
 * vendor adding a seventh code should read as a slightly generic refusal, never as a 500.
 */
/**
 * What we asked for, so a refusal can be checked against it. Optional: a caller that does not know
 * (or does not care) gets today's behaviour.
 */
export type Attempt = {
	/** The length of the password we sent. Never the password. */
	length?: number;
	/** The floor we believed applied — `MIN_PASSWORD_LENGTH`. */
	minLength?: number;
};

export function passwordRejection(error: unknown, attempt: Attempt = {}): string | null {
	// Codes and message together, because a 422 keeps the reason only in the message (above).
	// Hyphens and spaces folded to underscores so `PasswordTooWeak`, `password_too_weak` and
	// `password too weak` are one thing.
	const haystack = [...passwordErrorCodes(error), providerMessage(error).toLowerCase()]
		.join(' ')
		.replace(/[\s-]/g, '_');

	// THE LINK WINS. `password_reset_token_expired` contains both words, and an expired link is not
	// a bad password — saying otherwise sends somebody off changing the one thing that was fine.
	if (/token|expired|revoked|invitation/.test(haystack)) return null;

	for (const [needle, rule, message] of REASONS) {
		if (!haystack.includes(needle)) continue;
		// THE POLICY HAS MOVED, AND WE CAN PROVE IT. The provider calls a password too short that
		// already cleared the floor we believe in — so the dashboard's minimum is now above
		// `MIN_PASSWORD_LENGTH` and our mirror is stale. Repeating our number here is the worst
		// outcome available: the reader types exactly that many characters and is refused again,
		// with the advice itself as the bug. Say it without the number, and say so in the log where
		// somebody can fix the constant.
		const { length, minLength } = attempt;
		if (
			rule === 'too-short' &&
			length !== undefined &&
			minLength !== undefined &&
			length >= minLength
		) {
			console.error(
				'[auth] PASSWORD POLICY DIVERGED: the provider refused a password of ' +
					`${length} characters as too short, but MIN_PASSWORD_LENGTH is ${minLength}. ` +
					'Raise it to match the WorkOS dashboard (Authentication → Email + Password → ' +
					'Password policy → Minimum password length).'
			);
			return TOO_SHORT_UNKNOWN;
		}
		// What the provider ACTUALLY said, recorded once per rejection. The vendor's live body has
		// never been seen — every shape this module handles is inferred from the SDK's source — and
		// without this we would never learn, because a recognised rejection returns before the
		// `failed` branch that does the only other logging. No password is involved.
		console.warn('[auth] password rejected by the provider:', {
			rule,
			codes: passwordErrorCodes(error),
			providerMessage: providerMessage(error)
		});
		return message;
	}
	// Recognisably about the password, but a reason this build has no copy for: a seventh vendor
	// code should read as a slightly generic refusal, never as a 500. Logged LOUDLY, because this
	// is the case where the provider knows something we do not — it is the signal that a mapping
	// needs adding, and the raw codes are what to add it from.
	if (!haystack.includes('password')) return null;
	console.error('[auth] UNMAPPED password rejection — add a case to REASONS:', {
		codes: passwordErrorCodes(error),
		providerMessage: providerMessage(error)
	});
	return WEAK_PASSWORD_MESSAGE;
}

/**
 * Was this error the LINK being unusable rather than the password?
 *
 * Same instance-and-rawData sweep, for the same reason: an expired reset token is a 400
 * `BadRequestException`, which has no `rawData` either, so this degraded to the generic failure
 * exactly as the weak-password case did.
 */
export function linkRejection(error: unknown): boolean {
	const haystack = [...passwordErrorCodes(error), providerMessage(error).toLowerCase()].join(' ');
	return /token|invitation|expired|revoked/.test(haystack);
}
