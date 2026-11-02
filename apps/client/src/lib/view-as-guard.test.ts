/**
 * Turning a refused write into a dialog.
 *
 * The API is what stops the action; this only decides when to explain it. The two things worth
 * pinning down are that it explains the right refusals — a write this portal attempted — and
 * that it never swallows the response, because the page that made the call still has its own
 * error handling to run.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { guardWrites, viewAsBlocked } from './view-as-guard.svelte';

const respond = (status: number, body: string | null = null) => new Response(body, { status });

/** What SvelteKit answers a form action with: the outcome is in the body, not the status. */
const actionResult = (status: number) =>
	respond(200, JSON.stringify({ type: 'failure', status, data: '[{"error":1},"no"]' }));

function install(response: () => Response): { undo: () => void; inner: ReturnType<typeof vi.fn> } {
	const inner = vi.fn(async () => response());
	// @ts-expect-error — a test double for the global
	globalThis.window = { fetch: inner, location: { origin: 'https://portal.test' } };
	return { undo: guardWrites(), inner };
}

afterEach(() => {
	viewAsBlocked.open = false;
	// @ts-expect-error — clearing the double
	delete globalThis.window;
});

describe('a refused write', () => {
	it('raises the dialog when a proxy passes the API status through', async () => {
		const { undo } = install(() => respond(403));
		await window.fetch('/notifications/mark', { method: 'POST' });
		expect(viewAsBlocked.open).toBe(true);
		undo();
	});

	// The pages that upload and agree post to form actions, and SvelteKit answers those with 200
	// and the refusal inside the body. Reading only the status misses every one of them.
	it('raises the dialog when a form action carries the refusal in its body', async () => {
		const { undo } = install(() => actionResult(403));
		await window.fetch('/requests?/fulfilItem', { method: 'POST' });
		expect(viewAsBlocked.open).toBe(true);
		undo();
	});

	it('still returns the response, so the page keeps its own error handling', async () => {
		const { undo } = install(() => respond(403));
		const res = await window.fetch('/adviser/messages/send', { method: 'POST' });
		expect(res.status).toBe(403);
		undo();
	});

	it('leaves a form action’s body unread for the page that asked for it', async () => {
		const { undo } = install(() => actionResult(403));
		const res = await window.fetch('/requests?/fulfilItem', { method: 'POST' });
		expect(JSON.parse(await res.text()).status).toBe(403);
		undo();
	});

	it('is recognised on an absolute URL naming this portal', async () => {
		const { undo } = install(() => respond(403));
		await window.fetch('https://portal.test/notifications/mark', { method: 'POST' });
		expect(viewAsBlocked.open).toBe(true);
		undo();
	});
});

describe('what it leaves alone', () => {
	it('a read that is refused — that is about the data, not the session', async () => {
		const { undo } = install(() => respond(403));
		await window.fetch('/documents');
		expect(viewAsBlocked.open).toBe(false);
		undo();
	});

	it('a write that succeeded', async () => {
		const { undo } = install(() => respond(200));
		await window.fetch('/view-as/exit', { method: 'POST' });
		expect(viewAsBlocked.open).toBe(false);
		undo();
	});

	// A form action can fail for ordinary reasons — a missing file, an empty message — and those
	// are the page's to explain, not this.
	it('a form action that failed for some other reason', async () => {
		const { undo } = install(() => actionResult(422));
		await window.fetch('/requests?/fulfilItem', { method: 'POST' });
		expect(viewAsBlocked.open).toBe(false);
		undo();
	});

	it('a write refused by somewhere that is not this portal', async () => {
		const { undo } = install(() => respond(403));
		await window.fetch('https://elsewhere.test/thing', { method: 'POST' });
		expect(viewAsBlocked.open).toBe(false);
		undo();
	});

	it('every fetch once the guard is removed', async () => {
		const { undo } = install(() => respond(403));
		undo();
		await window.fetch('/notifications/mark', { method: 'POST' });
		expect(viewAsBlocked.open).toBe(false);
	});
});
