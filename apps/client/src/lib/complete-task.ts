import { deserialize } from '$app/forms';
import { invalidate } from '$app/navigation';
import { toast } from '@graphene/ui';
import type { ActionResult } from '@sveltejs/kit';

/**
 * Mark one of the client's action items done, from anywhere in the portal.
 *
 * The `completeTask` form action lives ONLY on `/` (routes/+page.server.ts) — composed pages
 * (any slug) invoke it programmatically rather than each route re-declaring an identical
 * action. This mirrors the old home page's `use:enhance` callback: refresh everything on
 * success (the item leaves every list at once), toast the failure otherwise. Runs in the
 * browser only (it's an event-handler path), so the global fetch is the right one.
 */
export async function completeTask(uid: string): Promise<void> {
	const body = new FormData();
	body.set('uid', uid);

	let result: ActionResult;
	try {
		const res = await fetch('/?/completeTask', {
			method: 'POST',
			// Tells SvelteKit this is a programmatic action call (no full-page form re-render).
			headers: { 'x-sveltekit-action': 'true' },
			body
		});
		result = deserialize(await res.text());
	} catch {
		toast.error('Something went wrong.');
		return;
	}

	if (result.type === 'success') {
		await invalidate('app:composed');
	} else {
		toast.error(
			result.type === 'failure'
				? String(result.data?.error ?? 'We couldn’t complete that. Please try again.')
				: 'Something went wrong.'
		);
	}
}
