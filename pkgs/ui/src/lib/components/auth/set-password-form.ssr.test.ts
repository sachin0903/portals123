/**
 * The set-password form, server-rendered.
 *
 * Two things worth pinning, both of which were wrong before this change: the inputs carried no
 * `minlength`, so a three-character password submitted and was refused a round trip later (the three
 * change-password forms have had it since they were built); and there was no strength affordance at
 * all. The meter must NOT appear on a server render — there is no password yet, and a bar rendered
 * at zero before a character is typed reads as a judgement on nothing.
 */

import { readFileSync } from 'node:fs';
import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import SetPasswordForm from './set-password-form.svelte';

const base = { token: 'tok_1', tokenKind: 'invitation' as const };

describe('SetPasswordForm (SSR)', () => {
	it('carries the token and its kind so the action knows which call to make', () => {
		const { body } = render(SetPasswordForm, { props: base });
		expect(body).toContain('name="token"');
		expect(body).toContain('value="tok_1"');
		expect(body).toContain('name="token_kind"');
		expect(body).toContain('value="invitation"');
	});

	it('states the length rule in the markup, not only after a rejection', () => {
		const { body } = render(SetPasswordForm, { props: { ...base, minLength: 10 } });
		// Both fields: confirming a password the browser would refuse is a wasted keystroke too.
		expect(body.match(/minlength="10"/g)?.length).toBe(2);
	});

	it('omits minlength entirely when the page does not pass one', () => {
		// The old behaviour, kept reachable: a caller that knows no floor must not assert one.
		const { body } = render(SetPasswordForm, { props: base });
		expect(body).not.toContain('minlength');
	});

	it('renders NO strength meter on the server, because nothing has been typed', () => {
		const { body } = render(SetPasswordForm, { props: { ...base, minLength: 10 } });
		expect(body).not.toContain('progressbar');
		expect(body).not.toContain('Very Weak');
		expect(body).not.toContain('Strong');
	});

	it('puts the strength meter AFTER both fields, not between them', () => {
		// A source scan, because SSR renders no meter at all (nothing has been typed) — so the only
		// way to pin the order is to read it. Between the two fields it split a pair that is one
		// act, and it pushed the confirm field down as you typed.
		const source = readFileSync(new URL('./set-password-form.svelte', import.meta.url), 'utf8');
		expect(source.indexOf('name="confirm"')).toBeLessThan(source.indexOf('<PasswordStrength'));
	});

	it('does NOT gate the submit, so the provider’s own refusals stay reachable', () => {
		// A button that cannot be pressed means the vendor's rejection path is never exercised, and
		// every message in `password-errors.ts` stays inferred from the SDK source rather than seen.
		// The notes under the meter still say what is wrong and the action re-validates, so nothing
		// unsafe reaches WorkOS — the user just finds out a round trip later.
		const source = readFileSync(new URL('./set-password-form.svelte', import.meta.url), 'utf8');
		expect(source).not.toContain('disabled={blocked}');
		// Asserted on the SOURCE, not the rendered markup: the button's class carries
		// `disabled:opacity-50` and friends, so every "is it disabled" test against the HTML matches
		// the Tailwind variants for the state rather than the state itself.
		expect(render(SetPasswordForm, { props: { ...base, minLength: 10 } }).body).toContain(
			'type="submit"'
		);
	});

	it('renders the refusal on the SERVER, not only after hydration', () => {
		// It was seeded in an `$effect`, which does not run during SSR — so the action's own
		// response came back with an empty alert and the message only appeared once JS booted.
		const source = readFileSync(new URL('./set-password-form.svelte', import.meta.url), 'utf8');
		expect(source).toContain('const refusal = $derived(');
		expect(source).not.toMatch(/\$effect\([^)]*refusal/);
		const { body } = render(SetPasswordForm, {
			props: { ...base, form: { error: 'This link has expired. Request a new one.' } }
		});
		expect(body).toContain('This link has expired. Request a new one.');
	});

	it('shows the provider’s refusal verbatim when the action returns one', () => {
		// The message is already ours — `passwordRejection` maps the vendor's code to copy — so the
		// form prints it rather than second-guessing it.
		const { body } = render(SetPasswordForm, {
			props: { ...base, form: { error: 'That password has appeared in a known data breach.' } }
		});
		expect(body).toContain('That password has appeared in a known data breach.');
	});
});
