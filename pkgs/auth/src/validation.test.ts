import { describe, expect, it } from 'vitest';
import { MIN_PASSWORD_LENGTH, PASSWORD_HINT, validateNewPassword } from './validation';

describe('validateNewPassword', () => {
	it('accepts a long, unremarkable passphrase', () => {
		expect(validateNewPassword('correct horse battery')).toBeNull();
		expect(validateNewPassword('hunter2hunter')).toBeNull();
	});

	it('rejects empty and short passwords, saying how to fix it', () => {
		expect(validateNewPassword('')).toBe('Password is required.');
		const short = validateNewPassword('short1');
		expect(short).toContain(`at least ${MIN_PASSWORD_LENGTH} characters`);
		expect(short).toContain('unrelated words');
		// The old 8-character floor is no longer enough.
		expect(validateNewPassword('eightchr')).not.toBeNull();
	});

	it('rejects an over-long password rather than hashing it', () => {
		expect(validateNewPassword('a'.repeat(257))).toBe('Password must be 256 characters or fewer.');
	});

	it('rejects whitespace padding masquerading as length', () => {
		expect(validateNewPassword('ab        ')).toBe('Password cannot be mostly spaces.');
	});

	it('rejects the commonest passwords with a specific reason', () => {
		expect(validateNewPassword('password123')).toContain('most commonly used');
		expect(validateNewPassword('Welcome123')).toContain('most commonly used');
	});

	it('rejects repeated characters and keyboard runs', () => {
		expect(validateNewPassword('aaaaaaaaaaaa')).toContain('repeated character');
		expect(validateNewPassword('abcdefghijkl')).toContain('run straight off the keyboard');
		expect(validateNewPassword('1234567890')).toContain('run straight off the keyboard');
		expect(validateNewPassword('qwertyuiopas')).toContain('run straight off the keyboard');
	});

	it('rejects a password built from the email address', () => {
		expect(validateNewPassword('margaret.cole!!', 'margaret.cole@firm.test')).toBe(
			'Password must not contain your email address.'
		);
		// Only when the local part is long enough to be meaningful.
		expect(validateNewPassword('ab-battery-horse', 'ab@firm.test')).toBeNull();
	});
});

describe('PASSWORD_HINT', () => {
	// The drift this catches: the admin change-password form said "At least 8 characters" while
	// validateNewPassword rejected anything under 10, so a 9-character password was accepted by the
	// browser and refused by the server. Every surface renders PASSWORD_HINT, and PASSWORD_HINT is
	// built from MIN_PASSWORD_LENGTH, so the two cannot disagree.
	it('states the same floor the validator enforces', () => {
		expect(PASSWORD_HINT).toContain(`at least ${MIN_PASSWORD_LENGTH} characters`);
		expect(PASSWORD_HINT).not.toMatch(/\b8 characters\b/);
	});

	it('is honest at the boundary', () => {
		const justUnder = 'a'.repeat(MIN_PASSWORD_LENGTH - 1);
		const atFloor = 'battery-horse-staple'.slice(0, MIN_PASSWORD_LENGTH);
		expect(validateNewPassword(justUnder)).not.toBeNull();
		expect(validateNewPassword(atFloor)).toBeNull();
	});
});

describe('the policy we are mirroring', () => {
	/**
	 * WorkOS exposes no API for the password policy (no endpoint, nothing in the SDK), so the
	 * dashboard is the source and this is a hand-kept mirror. Stated here so a change is deliberate
	 * rather than a number that quietly stops matching: Dashboard → Authentication → Email +
	 * Password → Password policy → **Minimum password length**.
	 *
	 * The score half of the same policy is mirrored by `MIN_ACCEPTED_SCORE` in `@graphene/ui` — it
	 * lives there because the meter runs in the browser and `@graphene/auth` is deliberately kept
	 * out of the client bundle.
	 */
	it('requires 10 characters, matching the dashboard', () => {
		expect(MIN_PASSWORD_LENGTH).toBe(10);
	});
});
