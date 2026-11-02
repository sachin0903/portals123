/**
 * The classifier, driven by doubles shaped like the SDK's REAL exceptions.
 *
 * Why this file exists: `auth.test.ts` proved the old classifier against
 * `{ rawData: { code: 'password_too_weak' } }` — a shape `@workos-inc/node` cannot produce for a
 * rejected password. A weak password is a 422, and `UnprocessableEntityException` has no `rawData`
 * at all, so the suite was green while every rejected password on the set-password page showed
 * "Could not set your password. Please try again." The doubles below are built from the SDK's own
 * constructor (lib/factory-*.mjs), so they fail if that assumption is ever wrong again.
 */

import { describe, expect, it } from 'vitest';
import { linkRejection, passwordRejection } from './password-errors';
import { MIN_PASSWORD_LENGTH, WEAK_PASSWORD_MESSAGE } from './validation';

/**
 * A faithful `UnprocessableEntityException`. Two properties are the whole point: there is NO
 * `rawData`, and when the body carries `errors[]` the SDK REPLACES the message with a list of
 * machine codes — so `message` is not prose and must never be shown to anybody.
 */
function unprocessable({ code, errors }: { code?: string; errors?: { code: string }[] }) {
	const e = new Error() as Error & { status: number; code?: string };
	e.name = 'UnprocessableEntityException';
	e.status = 422;
	e.message = 'Unprocessable entity';
	if (code) e.code = code;
	if (errors) {
		const requirement = errors.length === 1 ? 'requirement' : 'requirements';
		e.message = `The following ${requirement} must be met:\n`;
		for (const { code: c } of errors) e.message = e.message.concat(`\t${c}\n`);
	}
	return e;
}

/** A faithful `BadRequestException` — also no `rawData`, code and errors on the instance. */
function badRequest({ code, errors }: { code?: string; errors?: { code: string }[] }) {
	const e = new Error('Bad request') as Error & { status: number; code?: string; errors?: unknown };
	e.name = 'BadRequestException';
	e.status = 400;
	if (code) e.code = code;
	if (errors) e.errors = errors;
	return e;
}

describe('a rejected password is recognised on the REAL 422', () => {
	it('reads the code off the instance, where the SDK actually puts it', () => {
		// The regression in one line: this object has no `rawData`, and the old classifier read
		// nothing else.
		const error = unprocessable({ code: 'password_too_weak' });
		expect((error as { rawData?: unknown }).rawData).toBeUndefined();
		expect(passwordRejection(error)).toBe(WEAK_PASSWORD_MESSAGE);
	});

	it('reads the codes out of errors[], which is where the specific reasons live', () => {
		const error = unprocessable({ errors: [{ code: 'password_pwned' }] });
		expect(passwordRejection(error)).toMatch(/known data breach/i);
	});

	it('NEVER echoes the SDK message, which is a list of machine codes', () => {
		// `The following requirement must be met:\n\tpassword_too_weak\n` is what the SDK builds.
		// Passing it through — which the old code intended to do — prints that into the alert.
		const error = unprocessable({ errors: [{ code: 'password_too_weak' }] });
		const shown = passwordRejection(error) ?? '';
		expect(error.message).toContain('password_too_weak');
		expect(shown).not.toContain('password_too_weak');
		expect(shown).not.toContain('must be met');
	});

	it('names the SPECIFIC reason rather than defaulting to "weak"', () => {
		expect(passwordRejection(unprocessable({ code: 'PasswordPwned' }))).toMatch(/breach/i);
		expect(passwordRejection(unprocessable({ code: 'PasswordContainsEmail' }))).toMatch(
			/email address/i
		);
		expect(passwordRejection(unprocessable({ code: 'PasswordTooShort' }))).toContain(
			String(MIN_PASSWORD_LENGTH)
		);
		expect(passwordRejection(unprocessable({ code: 'PasswordTooLong' }))).toMatch(/too long/i);
		expect(passwordRejection(unprocessable({ code: 'PasswordMissingCharacterType' }))).toMatch(
			/mix of characters/i
		);
	});

	it('prefers the specific reason when a password is BOTH breached and weak', () => {
		// A breached password is also a weak one. "It is in a breach" is the actionable half —
		// adding punctuation does not fix it, and the generic message would invite exactly that.
		const error = unprocessable({
			errors: [{ code: 'password_too_weak' }, { code: 'password_pwned' }]
		});
		expect(passwordRejection(error)).toMatch(/breach/i);
	});

	it('still answers for a code this build has never seen', () => {
		// A seventh vendor code must read as a slightly generic refusal, never as a 500.
		expect(passwordRejection(unprocessable({ code: 'password_haunted' }))).toBe(
			WEAK_PASSWORD_MESSAGE
		);
	});

	it('still reads the old rawData shape, so nothing that worked stops working', () => {
		expect(passwordRejection({ rawData: { code: 'password_too_weak' } })).toBe(
			WEAK_PASSWORD_MESSAGE
		);
	});
});

describe('what is NOT a password rejection', () => {
	it('an expired link is the LINK, even though its code says "password"', () => {
		// `password_reset_token_expired` contains both words. Telling somebody their password is
		// weak when the link has expired sends them off changing the one thing that was fine.
		const error = badRequest({ code: 'password_reset_token_expired' });
		expect(passwordRejection(error)).toBeNull();
		expect(linkRejection(error)).toBe(true);
	});

	it('an outage is neither, so the caller can say so honestly', () => {
		const error = new Error('socket hang up');
		expect(passwordRejection(error)).toBeNull();
		expect(linkRejection(error)).toBe(false);
	});

	it('a revoked invitation is the link', () => {
		expect(linkRejection(unprocessable({ code: 'invitation_revoked' }))).toBe(true);
	});

	it('does not guess from a message that never mentions a password', () => {
		expect(passwordRejection(new Error('Internal server error'))).toBeNull();
	});
});
