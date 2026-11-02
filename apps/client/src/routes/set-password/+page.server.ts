import { env } from '$env/dynamic/private';
import { fail, redirect } from '@sveltejs/kit';
import {
	MIN_PASSWORD_LENGTH,
	PASSWORD_HINT,
	acceptInvitationAndSetPassword,
	confirmPasswordReset,
	notifyOnboardingComplete,
	validateNewPassword
} from '@graphene/auth';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url }) => {
	// The long-lived WorkOS invitation token comes from the emailed link.
	const invitationToken = url.searchParams.get('invitation_token');
	return {
		token: invitationToken ?? url.searchParams.get('token') ?? '',
		tokenKind: invitationToken ? ('invitation' as const) : ('reset' as const),
		// State the rule up front — the same minimum the action enforces, from the same constant —
		// so the user isn't guessing after a rejection. Passed through `data` to keep
		// `@graphene/auth` out of the client bundle.
		hint: PASSWORD_HINT,
		// The same constant as the `minlength` attribute, so the browser refuses a short password
		// before the round trip rather than after it. The three change-password forms have done
		// this since they were built; this page was the one that still made you submit to find out.
		minLength: MIN_PASSWORD_LENGTH
	};
};

export const actions: Actions = {
	default: async ({ request, locals, fetch, url }) => {
		const data = await request.formData();
		const token = String(data.get('token') ?? '');
		const tokenKind = String(data.get('token_kind') ?? 'reset');
		const password = String(data.get('password') ?? '');
		const confirm = String(data.get('confirm') ?? '');
		if (!token) return fail(400, { error: 'This link is invalid. Request a new one.' });
		if (password !== confirm) return fail(400, { error: 'Passwords do not match.' });
		const invalid = validateNewPassword(password);
		if (invalid) return fail(400, { error: invalid });

		const res =
			tokenKind === 'invitation'
				? await acceptInvitationAndSetPassword(locals.auth.workos, token, password)
				: await confirmPasswordReset(locals.auth.workos, token, password);
		if (res.status === 'ok') {
			// Push the pending→active transition into the mirror now, rather than leaving this person
			// showing as never-invited until the WorkOS poller (or an operator) catches up. The host
			// resolves which firm's project the claim is verified against. Best-effort by design — the
			// password is already set, so a failure here must never fail the page.
			await notifyOnboardingComplete({ workosUserId: res.userId, email: res.email }, url.host, {
				apiUrl: env.API_URL ?? '',
				internalToken: env.INTERNAL_API_TOKEN ?? '',
				fetch
			});
			redirect(303, '/login?set=1');
		}
		if (res.status === 'invalid')
			return fail(400, { error: 'This link has expired. Request a new one.' });
		if (res.status === 'weak') return fail(400, { error: res.message });
		return fail(500, { error: 'Could not set your password. Please try again.' });
	}
};
