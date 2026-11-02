import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const getDocument = vi.fn((): { promise: Promise<unknown>; destroy: () => Promise<void> } => ({
	promise: new Promise(() => {}),
	destroy: async () => {}
}));
vi.mock('pdfjs-dist', () => ({ GlobalWorkerOptions: {}, getDocument }));

import DocumentThumb from './document-thumb.svelte';

/** `reveal()` reports every observed element as visible. */
let observed: { cb: IntersectionObserverCallback; els: Element[] }[] = [];
function reveal(): void {
	for (const o of observed) {
		const entries = o.els.map((target) => ({ target, isIntersecting: true }));
		o.cb(entries as IntersectionObserverEntry[], {} as IntersectionObserver);
	}
	flushSync();
}

function respond(type: string, body: BodyInit | null = 'x', status = 200) {
	const fetchMock = vi.fn(
		async () => new Response(body, { status, headers: { 'content-type': type } })
	);
	vi.stubGlobal('fetch', fetchMock);
	return fetchMock;
}

beforeEach(() => {
	observed = [];
	getDocument.mockClear();
	vi.stubGlobal(
		'URL',
		Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:thumb'), revokeObjectURL: vi.fn() })
	);
	vi.stubGlobal(
		'IntersectionObserver',
		class {
			entry: { cb: IntersectionObserverCallback; els: Element[] };
			constructor(cb: IntersectionObserverCallback) {
				this.entry = { cb, els: [] };
				observed.push(this.entry);
			}
			observe(el: Element) {
				this.entry.els.push(el);
			}
			disconnect() {
				this.entry.els = [];
			}
		}
	);
	// `bind:clientWidth` on the HTML sheet needs one; jsdom has none.
	vi.stubGlobal(
		'ResizeObserver',
		class {
			observe() {}
			unobserve() {}
			disconnect() {}
		}
	);
});

let app: ReturnType<typeof mount> | undefined;
afterEach(() => {
	if (app) unmount(app);
	app = undefined;
	document.body.replaceChildren();
	vi.unstubAllGlobals();
});

type Props = { format?: 'pdf' | 'docx' | 'html'; status?: 'ready' | 'rendering' | 'failed' };
function show({ format = 'pdf', status }: Props = {}, src = '/documents/d1/content'): HTMLElement {
	const target = document.body.appendChild(document.createElement('div'));
	app = mount(DocumentThumb, { target, props: { src, format, status } });
	flushSync();
	return target;
}

const skeleton = (el: HTMLElement) => el.querySelector('[data-slot="document-thumb-skeleton"]');
const phase = (el: HTMLElement) =>
	el.querySelector('[data-slot="document-thumb"]')?.getAttribute('data-phase');

describe('DocumentThumb', () => {
	it('shows the paper skeleton and fetches nothing until near', async () => {
		const fetchMock = respond('application/pdf');
		const el = show();
		await new Promise((r) => setTimeout(r, 0));
		expect(skeleton(el)).not.toBeNull();
		expect(el.querySelector('svg')).toBeNull();
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it('draws a PDF from the fetched preview bytes with pdf.js', async () => {
		const fetchMock = respond('application/pdf', new Uint8Array([37, 80, 68, 70]));
		const el = show();
		reveal();
		await vi.waitFor(() => expect(getDocument).toHaveBeenCalledTimes(1));
		expect(fetchMock.mock.calls).toEqual([['/documents/d1/content?preview=1', expect.anything()]]);
		expect(getDocument).toHaveBeenCalledWith({ data: expect.any(Uint8Array) });
		expect(el.querySelector('canvas')).not.toBeNull();
		expect(skeleton(el)).not.toBeNull();
	});

	it('draws an image from an object URL, whatever the row was tagged', async () => {
		respond('image/png');
		const el = show({}, '/files/f1/download');
		reveal();
		await vi.waitFor(() => expect(el.querySelector('img')?.getAttribute('src')).toBe('blob:thumb'));
		el.querySelector('img')?.dispatchEvent(new Event('load'));
		flushSync();
		expect(skeleton(el)).toBeNull();
	});

	it('renders HTML in a sandboxed iframe', async () => {
		respond('text/html; charset=utf-8', '<p>I agree</p>');
		const el = show({ format: 'html' });
		reveal();
		await vi.waitFor(() => expect(el.querySelector('iframe')).not.toBeNull());
		expect(el.querySelector('iframe')?.getAttribute('sandbox')).toBe('');
		expect(el.querySelector('iframe')?.getAttribute('srcdoc')).toContain('I agree');
		expect(skeleton(el)).toBeNull();
	});

	it('shows the icon, and fetches nothing, for a docx', async () => {
		const fetchMock = respond('application/octet-stream');
		const el = show({ format: 'docx' });
		reveal();
		await tick();
		expect(phase(el)).toBe('icon');
		expect(el.querySelector('svg')).not.toBeNull();
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it.each([
		['a bodiless 204', '', null, 204],
		['an undrawable content-type', 'application/vnd.ms-excel', 'x', 200]
	])('shows the icon for %s', async (_, type, body, status) => {
		respond(type, body, status);
		const el = show();
		reveal();
		await vi.waitFor(() => expect(phase(el)).toBe('icon'));
		expect(el.textContent).not.toContain('Preview unavailable');
	});

	it.each([
		['the fetch fails', () => respond('application/json', '{}', 404)],
		[
			'pdf.js rejects the bytes',
			() => {
				respond('application/pdf');
				getDocument.mockImplementationOnce(() => ({
					promise: Promise.reject(new Error('Invalid PDF')),
					destroy: async () => {}
				}));
			}
		]
	])('shows "Preview unavailable" when %s', async (_, arrange) => {
		arrange();
		const el = show();
		reveal();
		await vi.waitFor(() => expect(el.textContent).toContain('Preview unavailable'));
		expect(skeleton(el)).toBeNull();
		expect(el.querySelector('canvas')).toBeNull();
	});

	it.each([
		['rendering', 'Rendering…'],
		['failed', 'Couldn’t render']
	] as const)('shows the %s state and fetches nothing', async (status, text) => {
		const fetchMock = respond('application/pdf');
		const el = show({ status });
		reveal();
		await tick();
		expect(el.textContent).toContain(text);
		expect(el.querySelector('canvas')).toBeNull();
		expect(fetchMock).not.toHaveBeenCalled();
	});
});
