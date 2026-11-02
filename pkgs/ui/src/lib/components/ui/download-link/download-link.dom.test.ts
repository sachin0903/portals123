import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, tick, unmount } from 'svelte';

const toast = vi.hoisted(() => ({
	loading: vi.fn(() => 'pending'),
	success: vi.fn(),
	error: vi.fn(),
	dismiss: vi.fn()
}));
vi.mock('svelte-sonner', () => ({ toast }));

const saveFile = vi.hoisted(() => vi.fn());
vi.mock('../../book/listing/export-download.js', async (original) => ({
	...(await original<typeof import('../../book/listing/export-download.js')>()),
	saveFile
}));

import DownloadLink from './download-link.svelte';

const children = createRawSnippet(() => ({ render: () => '<span>Client report</span>' }));

let component: ReturnType<typeof mount> | null = null;

function render(): HTMLAnchorElement {
	component = mount(DownloadLink, {
		target: document.body,
		props: { href: '/clients/c1/compliance/reports/client', name: 'Client report', children }
	});
	flushSync();
	return document.querySelector('a') as HTMLAnchorElement;
}

function respond(response: Response) {
	const fetchMock = vi.fn(async () => response);
	vi.stubGlobal('fetch', fetchMock);
	return fetchMock;
}

/** Lets the click's fetch, and the toast after it, settle. */
async function settle(): Promise<void> {
	for (let i = 0; i < 5; i += 1) await tick();
	await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
	vi.clearAllMocks();
});

afterEach(() => {
	if (component) unmount(component);
	component = null;
	document.body.innerHTML = '';
	vi.unstubAllGlobals();
});

describe('DownloadLink', () => {
	it('saves the file in place on a plain click, under the name the server gives', async () => {
		const fetchMock = respond(
			new Response('%PDF', {
				headers: {
					'content-type': 'application/pdf',
					'content-disposition': 'attachment; filename="client-report-c1.pdf"'
				}
			})
		);
		const link = render();
		expect(link.getAttribute('href')).toBe('/clients/c1/compliance/reports/client');
		expect(link.getAttribute('target')).toBeNull();

		const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
		link.dispatchEvent(click);
		await settle();

		expect(click.defaultPrevented).toBe(true);
		expect(fetchMock).toHaveBeenCalledWith(
			'/clients/c1/compliance/reports/client',
			expect.anything()
		);
		expect(saveFile).toHaveBeenCalledWith(expect.any(Blob), 'client-report-c1.pdf');
		expect(toast.loading).toHaveBeenCalledWith('Preparing Client report…');
		expect(toast.success).toHaveBeenCalledWith('Client report downloaded', { id: 'pending' });
	});

	it("shows the server's reason in a toast when the file cannot be made, and stays on the page", async () => {
		respond(
			new Response(JSON.stringify({ message: 'The report could not be made. Try again.' }), {
				status: 503,
				headers: { 'content-type': 'application/json' }
			})
		);
		const link = render();
		link.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
		await settle();

		expect(saveFile).not.toHaveBeenCalled();
		expect(toast.error).toHaveBeenCalledWith('The report could not be made. Try again.', {
			id: 'pending'
		});
	});

	it('names the file in its own message when the server gives no reason', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => {
				throw new Error('network down');
			})
		);
		const link = render();
		link.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
		await settle();

		expect(toast.error).toHaveBeenCalledWith('Client report could not be made. Try again.', {
			id: 'pending'
		});
	});

	it('leaves a modified click to the browser', async () => {
		const fetchMock = respond(new Response('%PDF'));
		const link = render();
		const click = new MouseEvent('click', {
			bubbles: true,
			cancelable: true,
			button: 0,
			ctrlKey: true
		});
		link.dispatchEvent(click);
		await settle();

		expect(click.defaultPrevented).toBe(false);
		expect(fetchMock).not.toHaveBeenCalled();
	});
});
