/**
 * Server-side validation for the auth forms. WorkOS is the ultimate authority
 * on email/password policy, but validating here gives fast, clear feedback and
 * defence-in-depth rather than relying on the client `type="email"` / `required`.
 */

/** Above WorkOS's own floor of 8: length is the one password rule that reliably buys
 * strength (NIST SP 800-63B), so we lean on it rather than composition rules. */
export const MIN_PASSWORD_LENGTH = 10;
/** Guard against a multi-megabyte "password" being hashed downstream. */
const MAX_PASSWORD_LENGTH = 256;

/**
 * The rule, stated up front. THE single copy of it: every surface that asks for a new password
 * (the emailed set-password page and all three change-password forms) renders this, so the hint,
 * the `minlength` attribute and `validateNewPassword` can never drift apart — an admin being told
 * "at least 8 characters" and then refused after a round trip is exactly what that drift looks
 * like. Built from MIN_PASSWORD_LENGTH so raising the floor updates every surface at once.
 */
export const PASSWORD_HINT =
	`Choose a password of at least ${MIN_PASSWORD_LENGTH} characters — ` +
	'three or four unrelated words make a strong, memorable one.';

/**
 * Shown when the identity provider rejects a password but names no reason we recognise.
 *
 * IT MUST NOT NAME A RULE THE POLICY MAY NOT HAVE ON. This used to say "it appears in a known
 * breach list or is too easy to guess" — but rejecting breached passwords is a per-project
 * checkbox in the WorkOS dashboard, and it is OFF for this deployment, so half that sentence was
 * a statement we could not support. Somebody reading it would go looking for a breach that never
 * happened. The breach case has its own message in `password-errors.ts` and is only ever used when
 * the provider actually says so.
 *
 * Still actionable: "choose a stronger password" tells nobody what to change, and guessability is
 * not fixed by adding punctuation — length and unpredictability are what the score measures.
 * Lives here rather than in `auth.ts` so the error classifiers can use it without an import cycle.
 */
export const WEAK_PASSWORD_MESSAGE =
	'That password was rejected as too easy to guess. ' +
	'Try a longer passphrase of three or four unrelated words.';

/** The handful of weak shapes a local check can catch honestly. WorkOS holds the real
 * breach corpus; this is not a substitute for it, just the obvious cases — caught before
 * the round trip so the message can say *why* rather than "choose a stronger password". */
const COMMON_PASSWORDS = new Set([
	'password1',
	'password12',
	'password123',
	'passw0rd123',
	'letmein123',
	'iloveyou12',
	'welcome123',
	'admin12345',
	'changeme123',
	'graphene123'
]);

/** True when the password is one character repeated, or a straight run off the keyboard. */
function isTrivialSequence(lower: string): boolean {
	if (/^(.)\1+$/.test(lower)) return true;
	const runs = ['abcdefghijklmnopqrstuvwxyz', '01234567890', 'qwertyuiopasdfghjklzxcvbnm'];
	const reversed = [...lower].reverse().join('');
	return runs.some((run) => run.includes(lower) || run.includes(reversed));
}

/**
 * First problem with a new password, or null if it looks acceptable.
 *
 * Messages say what to change rather than just "invalid": this runs on the set-password
 * (invite) page and the change-password form, where a generic rejection leaves the user
 * guessing. `email`, when known, is checked too — a password built from the address is the
 * commonest weak choice we can detect without a breach corpus.
 */
export function validateNewPassword(password: string, email?: string): string | null {
	if (!password) return 'Password is required.';
	if (password.length < MIN_PASSWORD_LENGTH)
		return `Password must be at least ${MIN_PASSWORD_LENGTH} characters — three or four unrelated words make a strong, memorable one.`;
	if (password.length > MAX_PASSWORD_LENGTH)
		return `Password must be ${MAX_PASSWORD_LENGTH} characters or fewer.`;
	if (password.trim().length < MIN_PASSWORD_LENGTH) return 'Password cannot be mostly spaces.';

	const lower = password.toLowerCase();
	if (COMMON_PASSWORDS.has(lower))
		return 'That is one of the most commonly used passwords — pick something that is not on any word list.';
	if (isTrivialSequence(lower))
		return 'That password is a single repeated character or a run straight off the keyboard — mix it up.';

	const local = (email ?? '').split('@')[0]?.toLowerCase() ?? '';
	if (local.length >= 3 && lower.includes(local))
		return 'Password must not contain your email address.';

	return null;
}
