import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { reactiveProps } from './identity-verification.harness.svelte.js';
import IdentityVerification from './identity-verification.svelte';
import { SUBMIT_FAILED, type IdentityCapture } from '$lib/identity-capture';

// The camera box opens every chosen PDF to count its pages (pdf.js, a dependency of @graphene/ui,
// so it is named by that package's path). A one-page PDF here.
vi.mock('../../../../../../pkgs/ui/node_modules/pdfjs-dist', () => ({
	GlobalWorkerOptions: {},
	getDocument: () => ({
		promise: Promise.resolve({ numPages: 1, getPage: async () => ({}) }),
		destroy: async () => {}
	})
}));

// The @graphene/ui barrel reaches `svelte/motion`, which reads `matchMedia` as it loads, and jsdom
// has none. Hoisted so it is in place before the imports run.
vi.hoisted(() => {
	window.matchMedia = ((query: string) => ({
		matches: false,
		media: query,
		onchange: null,
		addEventListener: () => {},
		removeEventListener: () => {},
		addListener: () => {},
		removeListener: () => {},
		dispatchEvent: () => false
	})) as unknown as typeof window.matchMedia;
});

// The document check runs two WebAssembly engines jsdom cannot; these flows need only its answer.
const { checkDocumentPhoto } = vi.hoisted(() => ({ checkDocumentPhoto: vi.fn() }));
vi.mock('@graphene/ui/identity-capture-ui', async (importOriginal) => ({
	...(await importOriginal<typeof import('@graphene/ui')>()),
	checkDocumentPhoto,
	prepareDocumentCheck: () => {}
}));

vi.useFakeTimers({ toFake: ['Date'] });

type Mounted = {
	host: HTMLElement;
	component: ReturnType<typeof mount>;
	/** The live props object passed to `mount`. Mutating a property on it and calling `flushSync`
	 *  updates the running component, the same way a parent's own re-render would. */
	props: Record<string, unknown>;
};

function captureState(overrides: Partial<IdentityCapture> = {}): IdentityCapture {
	return {
		items: [
			{ key: 'idv_document_front', item_uid: 'item-front', status: 'open' },
			{ key: 'idv_document_back', item_uid: 'item-back', status: 'open' },
			{ key: 'idv_selfie', item_uid: 'item-selfie', status: 'open' }
		],
		status: 'capturing',
		attempt: 1,
		last_reason: null,
		manual_offered: false,
		...overrides
	};
}

function render(overrides: Record<string, unknown> = {}): Mounted {
	const host = document.createElement('div');
	document.body.appendChild(host);
	// A reactive bag, not a plain object: a later `view.props.capture = …` only reaches the running
	// component (the same way a parent's own re-render would) if Svelte is tracking the assignment.
	const props = reactiveProps<Record<string, unknown>>({
		capture: captureState(),
		send: async () => null,
		...overrides
	});
	const component = mount(IdentityVerification, { target: host, props: props as never });
	flushSync();
	return { host, component, props };
}

function destroy({ host, component }: Mounted): void {
	unmount(component);
	host.remove();
}

function button(host: HTMLElement, text: string): HTMLButtonElement {
	const found = [...host.querySelectorAll('button')].find((b) => b.textContent?.trim() === text);
	if (!found) throw new Error(`No "${text}" button in: ${host.textContent}`);
	return found;
}

async function settle(): Promise<void> {
	for (let i = 0; i < 20; i++) await Promise.resolve();
	flushSync();
}

/** The label of the photo now being asked for (the camera component's group). */
function currentShot(host: HTMLElement): string | null {
	return host.querySelector('[role="group"]')?.getAttribute('aria-label') ?? null;
}

/** Take the current photo through the upload fallback (jsdom has no camera) and accept it. */
async function takePhoto(host: HTMLElement, name: string): Promise<void> {
	const input = host.querySelector('input[type="file"]') as HTMLInputElement;
	// A usual size: inside the check's window, so the camera box sends it as it is.
	const file = new File([new Uint8Array(50_000)], name, { type: 'image/jpeg' });
	Object.defineProperty(input, 'files', { value: [file], configurable: true });
	input.dispatchEvent(new Event('change', { bubbles: true }));
	await settle();
	button(host, 'Use photo').click();
	await settle();
}

/** What one send named, and the photos it carried as the item key and the file's name. */
function sentWith(
	send: ReturnType<typeof vi.fn>,
	call = 0
): { documentType: string; photos: [string, string][] } {
	const [documentType, photos] = send.mock.calls[call] as [string, Record<string, File>];
	return {
		documentType,
		photos: Object.entries(photos).map(([key, file]) => [key, file.name])
	};
}

/** Confirm the likeness and press send. */
async function confirmAndSubmit(host: HTMLElement): Promise<void> {
	(host.querySelector('[role="checkbox"]') as HTMLButtonElement).click();
	flushSync();
	button(host, 'Submit photos').click();
	await settle();
}

/** A phone link on offer and not yet asked for. */
function idlePhone() {
	return { link: null, busy: false, error: '', start: vi.fn(), cancel: vi.fn() };
}

/** Pick a photo ID by its card in the radio group. */
function documentCard(host: HTMLElement, documentLabel: string): HTMLButtonElement {
	const found = host.querySelector(`[role="radio"][aria-label="${documentLabel}"]`);
	if (!found) throw new Error(`No "${documentLabel}" card in: ${host.textContent}`);
	return found as HTMLButtonElement;
}

async function choose(host: HTMLElement, documentLabel: string): Promise<void> {
	documentCard(host, documentLabel).click();
	flushSync();
	button(host, 'Continue').click();
	await settle();
}

beforeEach(() => {
	checkDocumentPhoto.mockReset().mockResolvedValue([]);
	URL.createObjectURL = vi.fn(() => 'blob:photo');
	URL.revokeObjectURL = vi.fn();
	// jsdom decodes and draws no images: an upload is re-encoded through these.
	vi.stubGlobal(
		'createImageBitmap',
		vi.fn(async () => ({ width: 1600, height: 1200, close: () => {} }))
	);
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
		drawImage: vi.fn()
	} as unknown as CanvasRenderingContext2D);
	// Inside the check's size window, so each photo is encoded once.
	vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(function (callback) {
		callback(new Blob([new Uint8Array(50_000)], { type: 'image/jpeg' }));
	});
});

afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
	Reflect.deleteProperty(navigator, 'mediaDevices');
});

describe('IdentityVerification', () => {
	it('checks each side of the document as the chosen ID, and not the selfie', async () => {
		const view = render({});
		await choose(view.host, 'Driving licence');
		await takePhoto(view.host, 'front.jpg');
		await takePhoto(view.host, 'back.jpg');
		await takePhoto(view.host, 'face.jpg');

		expect(checkDocumentPhoto.mock.calls.map(([, expected]) => expected)).toEqual([
			{ kind: 'driving_licence', side: 'front' },
			{ kind: 'driving_licence', side: 'back' }
		]);
		expect(view.host.textContent).toContain('Check your photos');
		destroy(view);
	}, 30_000);

	it('asks before it uses a document photo the check doubts', async () => {
		checkDocumentPhoto.mockResolvedValue([
			'This looks like a driving licence, not a passport. Check you chose the right photo ID.'
		]);
		const view = render({});
		await choose(view.host, 'Passport');
		const input = view.host.querySelector('input[type="file"]') as HTMLInputElement;
		Object.defineProperty(input, 'files', {
			value: [new File([new Uint8Array(50_000)], 'front.jpg', { type: 'image/jpeg' })],
			configurable: true
		});
		input.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();

		expect(view.host.textContent).toContain('This looks like a driving licence, not a passport.');
		button(view.host, 'Use photo').click();
		await settle();
		expect(currentShot(view.host)).toBe('The photo page of your passport');
		expect(document.body.textContent).toContain('Use this photo anyway?');

		button(document.body, 'Use it anyway').click();
		await settle();
		expect(currentShot(view.host)).toBe('A photo of your face');
		destroy(view);
	}, 30_000);

	it('skips the back of a passport', async () => {
		const view = render({});
		await choose(view.host, 'Passport');

		expect(currentShot(view.host)).toBe('The photo page of your passport');
		await takePhoto(view.host, 'front.jpg');

		expect(currentShot(view.host)).toBe('A photo of your face');
		await takePhoto(view.host, 'face.jpg');

		expect(view.host.textContent).toContain('Check your photos');
		expect(view.host.querySelectorAll('li').length).toBe(2);
		destroy(view);
	}, 30_000);

	it('changes the ID from the review without closing, and keeps the selfie', async () => {
		const send = vi.fn(async () => null);
		const view = render({ send });
		await choose(view.host, 'Passport');
		await takePhoto(view.host, 'front.jpg');
		await takePhoto(view.host, 'face.jpg');
		expect(view.host.textContent).toContain('Check your photos');

		button(view.host, 'Change ID type').click();
		flushSync();
		expect(view.host.textContent).toContain('Choose your photo ID');
		expect(documentCard(view.host, 'Passport').getAttribute('aria-checked')).toBe('true');

		// Only the new document's photos are asked for: the face is the same.
		await choose(view.host, 'Driving licence');
		expect(currentShot(view.host)).toBe('The front of your driving licence');
		await takePhoto(view.host, 'licence-front.jpg');
		expect(currentShot(view.host)).toBe('The back of your driving licence');
		await takePhoto(view.host, 'licence-back.jpg');

		expect(view.host.textContent).toContain('Check your photos');
		expect(view.host.querySelectorAll('li').length).toBe(3);
		await confirmAndSubmit(view.host);
		expect(sentWith(send)).toEqual({
			documentType: 'driving_licence',
			photos: [
				['idv_document_front', 'id-front.jpg'],
				['idv_document_back', 'id-back.jpg'],
				['idv_selfie', 'selfie.jpg']
			]
		});
		destroy(view);
	}, 30_000);

	it('keeps every photo when the same ID is chosen again', async () => {
		const view = render({});
		await choose(view.host, 'Driving licence');
		await takePhoto(view.host, 'front.jpg');
		expect(currentShot(view.host)).toBe('The back of your driving licence');

		// Back from a later photo, and on the first one the way back is "Change ID type".
		expect(button(view.host, 'Back')).toBeTruthy();
		button(view.host, 'Change ID type').click();
		flushSync();
		await choose(view.host, 'Driving licence');

		// The front is in, so it picks up at the back.
		expect(currentShot(view.host)).toBe('The back of your driving licence');
		button(view.host, 'Back').click();
		flushSync();
		expect(currentShot(view.host)).toBe('The front of your driving licence');
		expect(() => button(view.host, 'Back')).toThrow();
		expect(button(view.host, 'Change ID type')).toBeTruthy();
		destroy(view);
	}, 30_000);

	it('shows one Back at a time: the camera one while the document camera is open', async () => {
		// A live view opens on a secure page only (on plain http a phone gets its own camera app).
		Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
		const stop = vi.fn();
		const stream = { getTracks: () => [{ stop }], getVideoTracks: () => [{ stop }] };
		Object.defineProperty(navigator, 'mediaDevices', {
			value: { getUserMedia: vi.fn(async () => stream) },
			configurable: true
		});
		vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
		const view = render({});
		await choose(view.host, 'Passport');
		const backs = () =>
			[...view.host.querySelectorAll('button')].filter((b) => b.textContent?.trim() === 'Back');

		// On the first photo's two choices, the way back is "Change ID type", not a Back.
		expect(backs()).toHaveLength(0);
		expect(button(view.host, 'Change ID type')).toBeTruthy();
		button(view.host, 'Take a photo').click();
		await settle();
		expect(view.host.querySelector('video')).not.toBeNull();
		expect(backs()).toHaveLength(1);

		// The camera's Back returns to the choices.
		backs()[0]?.click();
		await settle();
		expect(view.host.querySelector('video')).toBeNull();
		expect(button(view.host, 'Take a photo')).toBeTruthy();
		expect(backs()).toHaveLength(0);
		button(view.host, 'Change ID type').click();
		await settle();
		expect(view.host.textContent).toContain('Choose your photo ID');
		destroy(view);
	}, 30_000);

	it('offers an upload beside the camera for the document, takes a PDF scan, and shows it as a card', async () => {
		// The camera is refused when it is asked for, so only a request for it is observed.
		const getUserMedia = vi.fn(async () => {
			throw new DOMException('Permission denied', 'NotAllowedError');
		});
		Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
		Object.defineProperty(navigator, 'mediaDevices', {
			value: { getUserMedia },
			configurable: true
		});
		const send = vi.fn(async () => null);
		const view = render({ send });
		await choose(view.host, 'Passport');

		// The document step opens on the two choices, and no camera starts.
		expect(currentShot(view.host)).toBe('The photo page of your passport');
		expect(button(view.host, 'Take a photo')).toBeTruthy();
		expect(button(view.host, 'Upload a photo or PDF')).toBeTruthy();
		expect(getUserMedia).not.toHaveBeenCalled();

		const input = view.host.querySelector('input[type="file"]') as HTMLInputElement;
		const scan = new File([new Uint8Array(50_000)], 'passport scan.pdf', {
			type: 'application/pdf'
		});
		Object.defineProperty(input, 'files', { value: [scan], configurable: true });
		input.dispatchEvent(new Event('change', { bubbles: true }));
		(await vi.waitFor(() => button(view.host, 'Use file'))).click();
		await settle();

		// The face still opens the camera first.
		expect(currentShot(view.host)).toBe('A photo of your face');
		expect(getUserMedia).toHaveBeenCalledOnce();
		expect(view.host.querySelector('input[type="file"]')?.getAttribute('accept')).toBe('image/*');
		await takePhoto(view.host, 'face.jpg');

		expect(view.host.textContent).toContain('Check your photos');
		const [front, face] = [...view.host.querySelectorAll('li')];
		expect(front.querySelector('[data-slot="captured-file-card"]')?.textContent?.trim()).toBe(
			'id-front.pdf, 49 KB'
		);
		expect(front.querySelector('img')).toBeNull();
		expect(face.querySelector('img')).not.toBeNull();

		await confirmAndSubmit(view.host);

		expect(sentWith(send).photos).toEqual([
			['idv_document_front', 'id-front.pdf'],
			['idv_selfie', 'selfie.jpg']
		]);
		const sentScan = ((send.mock.calls[0] as unknown[])[1] as Record<string, File>)
			.idv_document_front;
		expect(sentScan.type).toBe('application/pdf');
		expect(sentScan.size).toBe(50_000);
		destroy(view);
	}, 30_000);

	it('offers each photo ID as a card that says which sides it needs', async () => {
		const view = render({});
		const passport = documentCard(view.host, 'Passport');
		const licence = documentCard(view.host, 'Driving licence');
		expect(passport.textContent).toContain('The photo page');
		expect(licence.textContent).toContain('Front and back');
		expect(button(view.host, 'Continue').disabled).toBe(true);

		licence.click();
		flushSync();

		expect(licence.getAttribute('aria-checked')).toBe('true');
		expect(passport.getAttribute('aria-checked')).toBe('false');
		expect(button(view.host, 'Continue').disabled).toBe(false);
		destroy(view);
	});

	it('asks for the back of a driving licence', async () => {
		const view = render({});
		await choose(view.host, 'Driving licence');
		await takePhoto(view.host, 'front.jpg');

		expect(currentShot(view.host)).toBe('The back of your driving licence');
		destroy(view);
	});

	it('will not submit until the likeness is confirmed, then sends every photo in one go', async () => {
		const send = vi.fn(async () => null);
		const view = render({ send });
		await choose(view.host, 'Passport');
		await takePhoto(view.host, 'front.jpg');
		await takePhoto(view.host, 'face.jpg');

		const submit = button(view.host, 'Submit photos');
		expect(submit.disabled).toBe(true);

		const likeness = view.host.querySelector('[role="checkbox"]') as HTMLButtonElement;
		likeness.click();
		flushSync();
		expect(likeness.getAttribute('aria-checked')).toBe('true');
		expect(button(view.host, 'Submit photos').disabled).toBe(false);

		button(view.host, 'Submit photos').click();
		await settle();

		// One send that names the document; a passport has no back to send.
		expect(send).toHaveBeenCalledOnce();
		expect(sentWith(send)).toEqual({
			documentType: 'passport',
			photos: [
				['idv_document_front', 'id-front.jpg'],
				['idv_selfie', 'selfie.jpg']
			]
		});
		expect(view.host.textContent).toContain('Thank you for uploading your documents');
		destroy(view);
	});

	it('shows the error and stays on the photos when they cannot be sent', async () => {
		const send = vi.fn(async () => SUBMIT_FAILED);
		const view = render({ send });
		await choose(view.host, 'Passport');
		await takePhoto(view.host, 'front.jpg');
		await takePhoto(view.host, 'face.jpg');
		await confirmAndSubmit(view.host);

		expect(view.host.textContent).not.toContain('Thank you for uploading your documents');
		expect(view.host.querySelector('[role="alert"]')?.textContent).toContain(
			'Your photos could not be sent'
		);
		destroy(view);
	});

	it('shows the API’s nationality sentence as it stands', async () => {
		const message =
			'We need your nationality before we can check your ID. Please ask your adviser to add it.';
		const view = render({ send: vi.fn(async () => message) });
		await choose(view.host, 'Passport');
		await takePhoto(view.host, 'front.jpg');
		await takePhoto(view.host, 'face.jpg');
		await confirmAndSubmit(view.host);

		expect(view.host.querySelector('[role="alert"]')?.textContent?.trim()).toBe(message);
		destroy(view);
	});

	it('keeps the photos when the send fails, so pressing send again sends them', async () => {
		// The API's 503: nothing was lost, and the client still holds every photo.
		const send = vi
			.fn<() => Promise<string | null>>()
			.mockResolvedValueOnce(SUBMIT_FAILED)
			.mockResolvedValue(null);
		const view = render({ send });
		await choose(view.host, 'Passport');
		await takePhoto(view.host, 'front.jpg');
		await takePhoto(view.host, 'face.jpg');
		await confirmAndSubmit(view.host);

		expect(view.host.textContent).not.toContain('Thank you for uploading your documents');
		expect(view.host.querySelector('[role="alert"]')?.textContent?.trim()).toBe(SUBMIT_FAILED);
		expect(view.host.querySelectorAll('li').length).toBe(2);
		expect(button(view.host, 'Submit photos').disabled).toBe(false);

		button(view.host, 'Submit photos').click();
		await settle();

		expect(send).toHaveBeenCalledTimes(2);
		expect(sentWith(send, 1)).toEqual(sentWith(send, 0));
		expect(sentWith(send, 1).photos).toEqual([
			['idv_document_front', 'id-front.jpg'],
			['idv_selfie', 'selfie.jpg']
		]);
		expect(view.host.textContent).toContain('Thank you for uploading your documents');
		destroy(view);
	});

	it('lands on the retake view when the check refuses a photo of the send, and asks only for it', async () => {
		// The page re-reads the capture before the send resolves: a 200 that is still a retake, at
		// the same try, with a new item for the refused selfie and the document's photos kept.
		const refused = captureState({
			status: 'retake',
			attempt: 1,
			document_type: 'driving_licence',
			items: [
				{ key: 'idv_document_front', item_uid: 'item-front', status: 'fulfilled' },
				{ key: 'idv_document_back', item_uid: 'item-back', status: 'fulfilled' },
				{ key: 'idv_selfie', item_uid: 'item-selfie-2', status: 'open' }
			]
		});
		const view = render({});
		const send = vi
			.fn<() => Promise<string | null>>()
			.mockImplementationOnce(async () => {
				view.props.capture = refused;
				return null;
			})
			.mockResolvedValue(null);
		view.props.send = send;
		flushSync();
		await choose(view.host, 'Driving licence');
		await takePhoto(view.host, 'front.jpg');
		await takePhoto(view.host, 'back.jpg');
		await takePhoto(view.host, 'face.jpg');
		await confirmAndSubmit(view.host);

		expect(sentWith(send).photos.map(([key]) => key)).toEqual([
			'idv_document_front',
			'idv_document_back',
			'idv_selfie'
		]);
		expect(view.host.textContent).not.toContain('Thank you for uploading your documents');
		expect(view.host.textContent).toContain('Try 1');
		expect(view.host.textContent).toContain('We need a new photo of your face.');

		button(view.host, 'Send new photos').click();
		await settle();
		expect(view.host.textContent).not.toContain('Choose your photo ID');
		expect(view.host.textContent).toContain('Photo 1 of 1');
		expect(currentShot(view.host)).toBe('A photo of your face');
		await takePhoto(view.host, 'face-again.jpg');
		expect(view.host.querySelectorAll('li').length).toBe(1);
		await confirmAndSubmit(view.host);

		expect(sentWith(send, 1)).toEqual({
			documentType: 'driving_licence',
			photos: [['idv_selfie', 'selfie.jpg']]
		});
		expect(view.host.textContent).toContain('Thank you for uploading your documents');
		destroy(view);
	}, 30_000);

	it('sends the client back again when the check refuses every photo of a retake', async () => {
		const view = render({
			capture: captureState({ status: 'retake', attempt: 1, last_reason: null })
		});
		// A refused document reopens both sides, and the selfie it never reached: every item is
		// new at the same try.
		view.props.send = vi.fn(async () => {
			view.props.capture = captureState({
				status: 'retake',
				attempt: 1,
				last_reason: null,
				items: [
					{ key: 'idv_document_front', item_uid: 'item-front-2', status: 'open' },
					{ key: 'idv_document_back', item_uid: 'item-back-2', status: 'open' },
					{ key: 'idv_selfie', item_uid: 'item-selfie-2', status: 'open' }
				]
			});
			return null;
		});
		flushSync();

		button(view.host, 'Send new photos').click();
		flushSync();
		await choose(view.host, 'Passport');
		await takePhoto(view.host, 'front.jpg');
		await takePhoto(view.host, 'face.jpg');
		await confirmAndSubmit(view.host);

		// Not the review of the photos just refused, ready to send again.
		expect(view.host.textContent).not.toContain('Check your photos');
		expect(view.host.textContent).not.toContain('Thank you for uploading your documents');
		expect(view.host.textContent).toContain('We could not verify your identity. Please try again.');

		button(view.host, 'Send new photos').click();
		flushSync();
		expect(view.host.textContent).toContain('Choose your photo ID');
		destroy(view);
	}, 30_000);

	it('shows the retake view with the reason and the try count', async () => {
		const view = render({
			capture: captureState({
				status: 'retake',
				attempt: 2,
				last_reason: 'The photo of your document is blurred.'
			})
		});

		expect(view.host.textContent).toContain('Try 2');
		expect(view.host.textContent).toContain('The photo of your document is blurred.');

		button(view.host, 'Send new photos').click();
		flushSync();
		expect(view.host.textContent).toContain('Choose your photo ID');
		destroy(view);
	});

	it('reads a fresh set of items at the same attempt as a new try, not the one already sent', async () => {
		const view = render({
			capture: captureState({ status: 'retake', attempt: 1, last_reason: null })
		});

		// Retake against the original items and send — this is the try the client is waiting on.
		button(view.host, 'Send new photos').click();
		flushSync();
		await choose(view.host, 'Passport');
		await takePhoto(view.host, 'front.jpg');
		await takePhoto(view.host, 'face.jpg');
		await confirmAndSubmit(view.host);
		expect(view.host.textContent).toContain('Thank you for uploading your documents');

		// The photos could not be sent: the API asks for them again with a FRESH set of items at the
		// SAME attempt and status — only the item uids move. That must read as the try sent back, not as the one just
		// sent still pending, so the client is not left on a screen nothing will move.
		view.props.capture = captureState({
			status: 'retake',
			attempt: 1,
			last_reason: null,
			items: [
				{ key: 'idv_document_front', item_uid: 'item-front-2', status: 'open' },
				{ key: 'idv_document_back', item_uid: 'item-back-2', status: 'open' },
				{ key: 'idv_selfie', item_uid: 'item-selfie-2', status: 'open' }
			]
		});
		flushSync();
		expect(view.host.textContent).not.toContain('Thank you for uploading your documents');
		expect(view.host.textContent).toContain('We could not verify your identity. Please try again.');

		// Take the photos again against the new items, and send.
		button(view.host, 'Send new photos').click();
		flushSync();
		await choose(view.host, 'Passport');
		await takePhoto(view.host, 'front.jpg');
		await takePhoto(view.host, 'face.jpg');
		await confirmAndSubmit(view.host);
		expect(view.host.textContent).toContain('Thank you for uploading your documents');

		// The same capture comes back again, unmoved — still waiting, not a second new try.
		view.props.capture = captureState({
			status: 'retake',
			attempt: 1,
			last_reason: null,
			items: [
				{ key: 'idv_document_front', item_uid: 'item-front-2', status: 'open' },
				{ key: 'idv_document_back', item_uid: 'item-back-2', status: 'open' },
				{ key: 'idv_selfie', item_uid: 'item-selfie-2', status: 'open' }
			]
		});
		flushSync();
		expect(view.host.textContent).toContain('Thank you for uploading your documents');
		destroy(view);
	});

	it('asks only for the face when the document’s photos were kept, with no document choice', async () => {
		const send = vi.fn(async () => null);
		const view = render({
			send,
			capture: captureState({
				status: 'retake',
				attempt: 1,
				document_type: 'driving_licence',
				items: [
					{ key: 'idv_document_front', item_uid: 'item-front', status: 'fulfilled' },
					{ key: 'idv_document_back', item_uid: 'item-back', status: 'fulfilled' },
					{ key: 'idv_selfie', item_uid: 'item-selfie-2', status: 'open' }
				]
			})
		});
		expect(view.host.textContent).toContain('We need a new photo of your face.');

		button(view.host, 'Send new photos').click();
		await settle();
		expect(view.host.textContent).not.toContain('Choose your photo ID');
		expect(view.host.textContent).toContain('Photo 1 of 1');
		expect(currentShot(view.host)).toBe('A photo of your face');
		// The document is fixed by the photos kept, so there is no choice to go back to.
		expect(view.host.textContent).not.toContain('Change ID type');
		await takePhoto(view.host, 'face.jpg');

		expect(view.host.textContent).toContain('Check your photos');
		expect(view.host.textContent).not.toContain('Change ID type');
		expect(view.host.querySelectorAll('li').length).toBe(1);
		await confirmAndSubmit(view.host);

		expect(sentWith(send)).toEqual({
			documentType: 'driving_licence',
			photos: [['idv_selfie', 'selfie.jpg']]
		});
		expect(view.host.textContent).toContain('Thank you for uploading your documents');

		// The face is refused again: a new item for it alone reads as the retake again, not as the
		// try just sent still pending.
		view.props.capture = captureState({
			status: 'retake',
			attempt: 1,
			document_type: 'driving_licence',
			items: [
				{ key: 'idv_document_front', item_uid: 'item-front', status: 'fulfilled' },
				{ key: 'idv_document_back', item_uid: 'item-back', status: 'fulfilled' },
				{ key: 'idv_selfie', item_uid: 'item-selfie-3', status: 'open' }
			]
		});
		flushSync();
		expect(view.host.textContent).not.toContain('Thank you for uploading your documents');
		expect(view.host.textContent).toContain('We need a new photo of your face.');
		destroy(view);
	}, 30_000);

	it('asks only for the document when the face was kept, and lets the client choose it', async () => {
		const send = vi.fn(async () => null);
		const view = render({
			send,
			capture: captureState({
				status: 'retake',
				attempt: 1,
				document_type: 'driving_licence',
				items: [
					{ key: 'idv_document_front', item_uid: 'item-front-2', status: 'open' },
					{ key: 'idv_document_back', item_uid: 'item-back-2', status: 'open' },
					{ key: 'idv_selfie', item_uid: 'item-selfie', status: 'fulfilled' }
				]
			})
		});
		expect(view.host.textContent).toContain('We need a new photo of your document.');

		button(view.host, 'Send new photos').click();
		await settle();
		expect(view.host.textContent).toContain('Choose your photo ID');
		button(view.host, 'Continue').click();
		await settle();
		expect(view.host.textContent).toContain('Photo 1 of 2');
		expect(currentShot(view.host)).toBe('The front of your driving licence');
		await takePhoto(view.host, 'front.jpg');
		expect(currentShot(view.host)).toBe('The back of your driving licence');
		await takePhoto(view.host, 'back.jpg');

		expect(view.host.textContent).toContain('Check your photos');
		expect(view.host.querySelectorAll('li').length).toBe(2);
		await confirmAndSubmit(view.host);

		expect(sentWith(send)).toEqual({
			documentType: 'driving_licence',
			photos: [
				['idv_document_front', 'id-front.jpg'],
				['idv_document_back', 'id-back.jpg']
			]
		});
		destroy(view);
	}, 30_000);

	it('shows the generic line when the retake carries no reason', async () => {
		const view = render({ capture: captureState({ status: 'retake', attempt: 3 }) });

		expect(view.host.textContent).toContain('Try 3');
		expect(view.host.textContent).toContain('We could not verify your identity. Please try again.');
		destroy(view);
	});

	it('waits while the check runs', async () => {
		const view = render({ capture: captureState({ status: 'submitted' }) });

		expect(view.host.textContent).toContain('Thank you for uploading your documents');
		expect(view.host.querySelector('[role="group"]')).toBeNull();
		destroy(view);
	});

	describe('a retake raised after a person reviewed the photos', () => {
		it('asks again for the selfie alone, with the document kept', async () => {
			const send = vi.fn(async () => null);
			const view = render({
				send,
				capture: captureState({ status: 'review' })
			});
			expect(view.host.textContent).toContain('Thank you for uploading your documents');

			// The review moves straight to a new try: only the selfie item is fresh, the document's
			// photos are kept.
			view.props.capture = captureState({
				status: 'retake',
				attempt: 2,
				document_type: 'driving_licence',
				last_reason:
					'Your face was not clearly visible in the selfie. Please retake it in good light.',
				items: [
					{ key: 'idv_document_front', item_uid: 'item-front', status: 'fulfilled' },
					{ key: 'idv_document_back', item_uid: 'item-back', status: 'fulfilled' },
					{ key: 'idv_selfie', item_uid: 'item-selfie-2', status: 'open' }
				]
			});
			flushSync();

			expect(view.host.textContent).toContain('Try 2');
			expect(view.host.textContent).toContain(
				'Your face was not clearly visible in the selfie. Please retake it in good light.'
			);
			expect(view.host.textContent).toContain('We need a new photo of your face.');

			button(view.host, 'Send new photos').click();
			await settle();
			expect(view.host.textContent).not.toContain('Choose your photo ID');
			expect(currentShot(view.host)).toBe('A photo of your face');
			await takePhoto(view.host, 'face.jpg');

			expect(view.host.textContent).toContain('Check your photos');
			expect(view.host.querySelectorAll('li').length).toBe(1);
			await confirmAndSubmit(view.host);

			expect(sentWith(send)).toEqual({
				documentType: 'driving_licence',
				photos: [['idv_selfie', 'selfie.jpg']]
			});
			destroy(view);
		}, 30_000);

		it('asks again for the whole document on a passport, and lets the client re-pick it', async () => {
			const send = vi.fn(async () => null);
			const view = render({
				send,
				capture: captureState({ status: 'review' })
			});

			// Both document items are fresh — the API raises a new back item even though a passport
			// has no back shot to take — and the selfie is kept.
			view.props.capture = captureState({
				status: 'retake',
				attempt: 2,
				document_type: 'passport',
				last_reason:
					'Part of your document was cut off. Make sure all four corners are in the photo.',
				items: [
					{ key: 'idv_document_front', item_uid: 'item-front-2', status: 'open' },
					{ key: 'idv_document_back', item_uid: 'item-back-2', status: 'open' },
					{ key: 'idv_selfie', item_uid: 'item-selfie', status: 'fulfilled' }
				]
			});
			flushSync();

			expect(view.host.textContent).toContain(
				'Part of your document was cut off. Make sure all four corners are in the photo.'
			);
			expect(view.host.textContent).toContain('We need a new photo of your document.');

			button(view.host, 'Send new photos').click();
			flushSync();
			expect(view.host.textContent).toContain('Choose your photo ID');
			expect(documentCard(view.host, 'Passport').getAttribute('aria-checked')).toBe('true');

			button(view.host, 'Continue').click();
			await settle();
			expect(view.host.textContent).toContain('Photo 1 of 1');
			expect(currentShot(view.host)).toBe('The photo page of your passport');
			await takePhoto(view.host, 'front.jpg');

			expect(view.host.textContent).toContain('Check your photos');
			expect(view.host.querySelectorAll('li').length).toBe(1);
			await confirmAndSubmit(view.host);

			// The back item is open too, but a passport has no back to send.
			expect(sentWith(send)).toEqual({
				documentType: 'passport',
				photos: [['idv_document_front', 'id-front.jpg']]
			});
			destroy(view);
		}, 30_000);

		it('asks again for everything with no specific reason, and offers the document choice', async () => {
			const view = render({
				capture: captureState({ status: 'review' })
			});

			// A full retake: every item is fresh and nothing is kept, with no reason given.
			view.props.capture = captureState({
				status: 'retake',
				attempt: 2,
				last_reason: null,
				items: [
					{ key: 'idv_document_front', item_uid: 'item-front-2', status: 'open' },
					{ key: 'idv_document_back', item_uid: 'item-back-2', status: 'open' },
					{ key: 'idv_selfie', item_uid: 'item-selfie-2', status: 'open' }
				]
			});
			flushSync();

			expect(view.host.textContent).toContain(
				'We could not verify your identity. Please try again.'
			);
			expect(view.host.textContent).not.toContain('We need a new photo of your document.');
			expect(view.host.textContent).not.toContain('We need a new photo of your face.');

			button(view.host, 'Send new photos').click();
			flushSync();
			expect(view.host.textContent).toContain('Choose your photo ID');
			destroy(view);
		});
	});

	describe('continuing on a phone', () => {
		it('offers the phone, and once the link is up it shows the code instead of the camera', async () => {
			const phone = {
				link: null as { url: string; qrSvg: string } | null,
				busy: false,
				error: '',
				start: vi.fn(),
				cancel: vi.fn()
			};
			const view = render({ phone });

			// The phone is offered in a box of its own beside the photo ID, never in front of it.
			expect(view.host.textContent).toContain('Prefer to use your phone?');
			expect(view.host.textContent).toContain('Choose your photo ID');
			expect(view.host.textContent).toContain('Please use an original, in date, document.');
			button(view.host, 'Continue on your phone').click();
			expect(phone.start).toHaveBeenCalledOnce();

			view.props.phone = {
				...phone,
				link: {
					url: 'https://portal.test/i/once',
					qrSvg: '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
				}
			};
			flushSync();

			expect(view.host.textContent).toContain('https://portal.test/i/once');
			expect(view.host.textContent).toContain('Waiting for the other device…');
			expect(view.host.textContent).toContain('Photo of the document');
			expect(view.host.textContent).toContain('Waiting');
			expect(view.host.textContent).not.toContain('Choose your photo ID');

			view.props.capture = captureState({
				items: [
					{ key: 'idv_document_front', item_uid: 'item-front', status: 'fulfilled' },
					{ key: 'idv_document_back', item_uid: 'item-back', status: 'open' },
					{ key: 'idv_selfie', item_uid: 'item-selfie', status: 'open' }
				]
			});
			flushSync();
			expect(view.host.textContent).toContain('Received');
			expect(view.host.textContent).toContain('Photos are arriving from the other device.');
			const qr = view.host.querySelector('[aria-label="QR code to open the page on your phone"]');
			expect(qr?.querySelector('svg')).toBeTruthy();
			expect(view.host.querySelector('img')).toBeNull();

			button(view.host, 'Take the photos on this device instead').click();
			expect(phone.cancel).toHaveBeenCalledOnce();
			destroy(view);
		});

		it('shows why the link could not be made, under the offer', () => {
			const view = render({ phone: { ...idlePhone(), error: 'That didn’t work.' } });
			expect(view.host.querySelector('[role="alert"]')?.textContent).toBe('That didn’t work.');
			destroy(view);
		});

		it('offers a touch device another device, worded for a phone already in the hand', () => {
			const desktop = window.matchMedia;
			window.matchMedia = ((query: string) => ({
				...desktop(query),
				matches: query === '(pointer: coarse)'
			})) as typeof window.matchMedia;
			try {
				const phone = idlePhone();
				const view = render({ phone });
				expect(view.host.textContent).toContain('Prefer to use another device?');
				expect(view.host.textContent).not.toContain('Continue on your phone');
				expect(view.host.textContent).toContain('Choose your photo ID');
				button(view.host, 'Continue on another device').click();
				expect(phone.start).toHaveBeenCalledOnce();
				destroy(view);
			} finally {
				window.matchMedia = desktop;
			}
		});

		it('shows each photo the phone has taken, and no back for a passport', () => {
			const view = render({
				phone: {
					link: { url: 'https://portal.test/i/once', qrSvg: '<svg></svg>' },
					busy: false,
					error: '',
					start: vi.fn(),
					cancel: vi.fn()
				},
				capture: captureState({
					phone_progress: { document_type: 'passport', taken: ['idv_document_front'] }
				})
			});
			const rows = [...view.host.querySelectorAll('li')].map((li) =>
				li.textContent?.replace(/\s+/g, ' ').trim()
			);
			expect(rows).toEqual(['Photo of the document Taken', 'Photo of your face Waiting']);
			expect(view.host.textContent).toContain('Photos are arriving from the other device.');
			destroy(view);
		});

		it('says what this page has taken, and for which document, as it changes', async () => {
			const ontaken = vi.fn();
			const view = render({ ontaken });
			expect(ontaken).toHaveBeenLastCalledWith([], null);

			await choose(view.host, 'Passport');
			expect(ontaken).toHaveBeenLastCalledWith([], 'passport');
			await takePhoto(view.host, 'front.jpg');
			expect(ontaken).toHaveBeenLastCalledWith(['idv_document_front'], 'passport');
			await takePhoto(view.host, 'face.jpg');
			expect(ontaken).toHaveBeenLastCalledWith(['idv_document_front', 'idv_selfie'], 'passport');

			// A new document drops its photos, and the selfie stays.
			button(view.host, 'Change ID type').click();
			flushSync();
			await choose(view.host, 'Driving licence');
			expect(ontaken).toHaveBeenLastCalledWith(['idv_selfie'], 'driving_licence');
			destroy(view);
		}, 30_000);
	});
});
