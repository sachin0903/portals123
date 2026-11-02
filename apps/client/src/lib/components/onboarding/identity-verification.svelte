<script lang="ts">
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Button,
		CameraCapture,
		CapturedFileCard,
		Checkbox,
		checkDocumentPhoto,
		cn,
		isPdfFile,
		prepareDocumentCheck
	} from '@graphene/ui/identity-capture-ui';
	import CircleAlertIcon from '@lucide/svelte/icons/circle-alert';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import RotateCcwIcon from '@lucide/svelte/icons/rotate-ccw';
	import MonitorIcon from '@lucide/svelte/icons/monitor';
	import SmartphoneIcon from '@lucide/svelte/icons/smartphone';
	import BookUserIcon from '@lucide/svelte/icons/book-user';
	import CarFrontIcon from '@lucide/svelte/icons/car-front';
	import IdCardIcon from '@lucide/svelte/icons/id-card';
	import {
		attemptLabel,
		captureItemLabel,
		phoneWaitingList,
		captureItemsKey,
		IDENTITY_DOCUMENT_OPTIONS,
		identityShots,
		keptDocumentType,
		photosToSend,
		retakeNeeds,
		retakeReason,
		shotsToTake,
		type IdentityCapture,
		type IdentityCaptureKey,
		type IdentityDocumentType,
		type IdentityShot
	} from '$lib/identity-capture';

	/**
	 * The client's own identity capture: pick the document, photograph it (front, then back unless
	 * it is a passport) or upload a photo or PDF scan of it, photograph their face, confirm the
	 * likeness, and send every photo in one go. A retake that kept some photos asks only for the
	 * rest, and skips the document choice when the document's photos are kept. What happens after
	 * is the API's: this component shows the `capture` it is given and never decides an outcome.
	 */
	let {
		capture,
		send,
		phone = null,
		ontaken
	}: {
		/** The capture's current state, re-read by the page after a send and while a check runs. */
		capture: IdentityCapture;
		/** Name the document and send the photos the try still needs, in one request. Resolves null
		 *  when they are sent, or the error to show; the client keeps their photos to send again. A
		 *  photo the check refused comes back as a retake in the re-read `capture`, not as an error. */
		send: (
			documentType: IdentityDocumentType,
			photos: Partial<Record<IdentityCaptureKey, File>>
		) => Promise<string | null>;
		/** The photos this page holds now, and the document they are of, whenever either changes.
		 *  The phone page passes it on, so the computer waiting on the phone shows each arrive. */
		ontaken?: (keys: IdentityCaptureKey[], documentType: IdentityDocumentType | null) => void;
		/** Continue the photos on a phone. Absent on the phone page itself. */
		phone?: {
			link: { url: string; qrSvg: string } | null;
			busy: boolean;
			error: string;
			start: () => void;
			cancel: () => void;
		} | null;
	} = $props();

	const DOCUMENT_ICONS = {
		passport: BookUserIcon,
		driving_licence: CarFrontIcon,
		national_id: IdCardIcon
	} as const;

	type Stage = 'type' | 'shot' | 'review';

	let stage = $state<Stage>('type');
	let documentType = $state<IdentityDocumentType | undefined>(undefined);
	let shotIndex = $state(0);
	/** A single photo retaken from the review goes straight back to the review. */
	let returnToReview = $state(false);
	let photos = $state.raw<Partial<Record<IdentityCaptureKey, File>>>({});
	let likeness = $state(false);
	let sending = $state(false);
	let error = $state('');
	/** The capture as it stood when this client sent their photos — attempt, status and its items.
	 *  Until the API moves it on (a new status, a new try, or new items at the SAME attempt after a
	 *  refused photo or a technical failure), they are waiting for the result. */
	let sent = $state<{ attempt: number; status: string; itemsKey: string } | null>(null);
	/** The sent-back try the client chose to retake, and its items when they did. Items that move
	 *  after that send the client back again. */
	let retakeAttempt = $state<number | null>(null);
	let retakeItemsKey = $state<string | null>(null);
	let thumbnails = $state<Partial<Record<IdentityCaptureKey, string>>>({});
	/** The document camera is open, with its own Back to the two choices. */
	let cameraOpen = $state(false);
	/** A touch device is usually the phone already, so the other device is a computer or a tablet
	 *  there: the offer of it is worded for where the client is. */
	const touch =
		typeof window !== 'undefined' && (window.matchMedia?.('(pointer: coarse)').matches ?? false);
	const HereIcon = touch ? SmartphoneIcon : MonitorIcon;
	const ThereIcon = touch ? MonitorIcon : SmartphoneIcon;
	const phoneLabel = touch ? 'Continue on another device' : 'Continue on your phone';

	let copied = $state(false);

	$effect(() => {
		if (!ontaken) return;
		ontaken(
			shots.filter((s) => photos[s.key]).map((s) => s.key),
			documentType ?? null
		);
	});

	/** Painted as markup. Safari does not render an SVG offered as a data-URL image. */
	function qrMarkup(svg: string): string {
		const trimmed = svg.trim();
		if (!trimmed.startsWith('<svg') || /<script/i.test(trimmed)) return '';
		return trimmed;
	}

	async function copyLink(url: string): Promise<void> {
		try {
			await navigator.clipboard.writeText(url);
			copied = true;
		} catch {
			copied = false;
		}
	}

	const itemsKey = $derived(captureItemsKey(capture));
	/** The document a retake keeps: its photos are in, so the client is not asked to choose. */
	const keptType = $derived(keptDocumentType(capture));
	const allShots = $derived(documentType ? identityShots(documentType) : []);
	/** The shots still to take: a photo already in is not asked for again. */
	const shots = $derived(shotsToTake(allShots, capture.items));
	const shot = $derived(shots[shotIndex] ?? null);
	const allTaken = $derived(allShots.length > 0 && shots.every((s) => photos[s.key]));
	const canSubmit = $derived(allTaken && likeness && !sending);

	const view = $derived.by(() => {
		if (capture.status === 'passed') return 'passed';
		if (capture.status === 'review') return 'review';
		if (
			capture.status === 'submitted' ||
			(sent?.attempt === capture.attempt &&
				sent.status === capture.status &&
				sent.itemsKey === itemsKey)
		) {
			return 'waiting';
		}
		// A try sent back says why until the client starts on it. New items at the same try since
		// then (a photo the check refused, or a check that could not run) send them back again: the
		// photos they hold were sent, and the photos asked for are new ones.
		if (
			capture.status === 'retake' &&
			(retakeAttempt !== capture.attempt || retakeItemsKey !== itemsKey)
		) {
			return 'retake';
		}
		return 'capture';
	});

	function selectType(value: string): void {
		if (value === documentType) return;
		documentType = value as IdentityDocumentType;
		// The face is the same whatever the document, so a selfie already taken stays.
		photos = photos.idv_selfie ? { idv_selfie: photos.idv_selfie } : {};
		likeness = false;
	}

	/** The first shot after `after` with no photo yet, or -1 when every one is taken. */
	function nextOpen(after: number): number {
		return shots.findIndex((s, i) => i > after && !photos[s.key]);
	}

	/** Start on the first photo not yet taken: the same document chosen again keeps its photos. */
	function begin(): void {
		if (!documentType) return;
		const next = nextOpen(-1);
		shotIndex = Math.max(next, 0);
		returnToReview = false;
		error = '';
		stage = next === -1 ? 'review' : 'shot';
	}

	/** Back to the document choice, from any photo or the review, without closing. */
	function changeType(): void {
		returnToReview = false;
		error = '';
		stage = 'type';
	}

	function back(): void {
		if (returnToReview) {
			returnToReview = false;
			stage = 'review';
		} else if (shotIndex > 0) {
			shotIndex -= 1;
		}
	}

	/** The rough look at a document photo before it is used (`checkDocumentPhoto`): whether it
	 *  shows a face where the chosen document's side has one, and its print names that document.
	 *  It only warns. The selfie is not checked here. */
	function checkFor(s: IdentityShot): ((photo: File) => Promise<string[]>) | undefined {
		const kind = documentType;
		if (s.mode !== 'document' || !kind) return undefined;
		const side = s.key === 'idv_document_back' ? 'back' : 'front';
		return (photo) => checkDocumentPhoto(photo, { kind, side });
	}

	function onShot(file: File): void {
		if (!shot) return;
		photos = { ...photos, [shot.key]: file };
		likeness = false;
		const next = nextOpen(shotIndex);
		if (returnToReview || next === -1) {
			returnToReview = false;
			stage = 'review';
		} else {
			shotIndex = next;
		}
	}

	function retakeOne(index: number): void {
		shotIndex = index;
		returnToReview = true;
		error = '';
		stage = 'shot';
	}

	function startRetake(): void {
		retakeAttempt = capture.attempt;
		retakeItemsKey = itemsKey;
		photos = {};
		likeness = false;
		shotIndex = 0;
		returnToReview = false;
		error = '';
		if (keptType) {
			documentType = keptType;
			stage = shots.length ? 'shot' : 'review';
		} else {
			documentType = capture.document_type ?? documentType;
			stage = 'type';
		}
	}

	async function submit(): Promise<void> {
		if (!canSubmit || !documentType) return;
		const before = { attempt: capture.attempt, status: capture.status, itemsKey };
		const { send: needed, missing } = photosToSend(shots, capture.items);
		if (missing.length) {
			error = 'This check is not ready for your photos yet. Close this and try again later.';
			return;
		}
		// Only the open photos: the check already holds the rest of this try, and a passport has no
		// back to send.
		const files: Partial<Record<IdentityCaptureKey, File>> = {};
		for (const s of needed) {
			const file = photos[s.key];
			if (file) files[s.key] = file;
		}
		sending = true;
		error = '';
		try {
			const failure = await send(documentType, files);
			if (failure) {
				error = failure;
				return;
			}
			sent = before;
		} finally {
			sending = false;
		}
	}

	// The document check's two engines start loading as soon as a document photo is asked for,
	// so the photo is checked at once when it is taken.
	$effect(() => {
		if (stage === 'shot' && shot?.mode === 'document') prepareDocumentCheck();
	});

	// Previews for the review, released when the review closes or a photo changes.
	$effect(() => {
		if (stage !== 'review') return;
		const urls: Partial<Record<IdentityCaptureKey, string>> = {};
		for (const s of shots) {
			const file = photos[s.key];
			// A PDF scan is shown as a file card, not drawn.
			if (file && !isPdfFile(file)) urls[s.key] = URL.createObjectURL(file);
		}
		thumbnails = urls;
		return () => {
			for (const url of Object.values(urls)) if (url) URL.revokeObjectURL(url);
		};
	});
</script>

<!-- A retake that kept the document's photos has no choice to go back to. -->
{#snippet changeTypeButton()}
	{#if !keptType}
		<Button variant="ghost" size="sm" disabled={sending} onclick={changeType}>
			Change ID type
		</Button>
	{/if}
{/snippet}

<div class="space-y-4">
	{#if view === 'passed'}
		<div class="flex flex-col items-center gap-3 rounded border px-6 py-10 text-center">
			<div class="bg-success/10 text-success flex size-12 items-center justify-center rounded-full">
				<CircleCheckIcon class="size-6" />
			</div>
			<p class="font-medium">Your identity is verified</p>
		</div>
	{:else if view === 'review' || view === 'waiting'}
		<!-- Deliberately not "verified": the result is the API's, and it is not in yet. The client has
		     nothing to do while the photos are checked, by the service or by a person, so both read the same. -->
		<div class="flex flex-col items-center gap-3 rounded border px-6 py-10 text-center">
			<div class="bg-primary/10 text-primary flex size-12 items-center justify-center rounded-full">
				<CircleCheckIcon class="size-6" />
			</div>
			<p class="font-medium" role="status">Thank you for uploading your documents</p>
			<p class="text-muted-foreground max-w-sm text-sm">
				We will let you know if we need anything else. You can close this now.
			</p>
		</div>
	{:else if phone?.link}
		<div class="space-y-4">
			<div class="space-y-1 text-center">
				<p class="font-medium">Use another device?</p>
				<p class="text-muted-foreground text-sm">
					Scan this code, or open the link, to take the photos on another device. No sign-in is
					necessary. The link works once.
				</p>
			</div>
			<div class="flex justify-center">
				<div
					class="size-48 rounded bg-white p-2 [&_svg]:size-full"
					role="img"
					aria-label="QR code to open the page on your phone"
				>
					<!-- eslint-disable-next-line svelte/no-at-html-tags -- segno SVG from our own API. qrMarkup refuses anything that is not an <svg> or that contains a script. A data-URL image is not painted by Safari. -->
					{@html qrMarkup(phone.link.qrSvg)}
				</div>
			</div>
			<p class="text-center font-mono text-xs break-all">{phone.link.url}</p>
			<div class="flex flex-wrap items-center justify-center gap-2">
				<Button variant="outline" onclick={() => phone?.link && copyLink(phone.link.url)}>
					{copied ? 'Copied' : 'Copy link'}
				</Button>
				<Button variant="outline" disabled={phone.busy} onclick={() => phone?.cancel()}>
					<HereIcon /> Take the photos on this device instead
				</Button>
			</div>
			{#if phone.error}
				<p class="text-destructive text-center text-xs" role="alert">{phone.error}</p>
			{/if}
			<ul class="mx-auto max-w-sm space-y-1 text-sm">
				{#each phoneWaitingList(capture) as item (item.itemUid)}
					<li class="flex items-center justify-between gap-3">
						<span>{captureItemLabel(item.key)}</span>
						<span class={item.state === 'waiting' ? 'text-muted-foreground' : 'text-foreground'}>
							{item.state === 'received'
								? 'Received'
								: item.state === 'taken'
									? 'Taken'
									: 'Waiting'}
						</span>
					</li>
				{/each}
			</ul>
			<p class="text-muted-foreground text-center text-sm" role="status">
				{phoneWaitingList(capture).some((item) => item.state !== 'waiting')
					? 'Photos are arriving from the other device.'
					: 'Waiting for the other device…'}
			</p>
		</div>
	{:else if view === 'retake'}
		<Alert variant="warning">
			<CircleAlertIcon />
			<AlertTitle>{attemptLabel(capture.attempt)}</AlertTitle>
			<AlertDescription>
				<p>{retakeReason(capture.last_reason)}</p>
				{#if retakeNeeds(capture.items)}
					<p>{retakeNeeds(capture.items)}</p>
				{/if}
			</AlertDescription>
		</Alert>
		<div class="flex flex-wrap justify-end gap-2">
			{#if phone}
				<Button variant="outline" disabled={phone.busy} onclick={phone.start}>
					<ThereIcon />
					{phone.busy ? 'Preparing the link…' : phoneLabel}
				</Button>
			{/if}
			<Button onclick={startRetake}>
				<RotateCcwIcon /> Send new photos
			</Button>
		</div>
		{#if phone?.error}
			<p class="text-destructive text-xs" role="alert">{phone.error}</p>
		{/if}
	{:else}
		{#if phone}
			<!-- Offered beside the photos, never in their way: the photo ID cards draw the eye, so the
			     other device needs a box of its own to be seen, not a small button in a corner. -->
			<div
				class="bg-muted/40 flex flex-wrap items-center gap-3 rounded-lg border p-3 sm:flex-nowrap"
			>
				<span
					class="bg-primary/10 text-primary flex size-9 shrink-0 items-center justify-center rounded-md"
				>
					<ThereIcon class="size-5" />
				</span>
				<div class="min-w-0 flex-1">
					<p class="text-sm font-medium">
						{touch ? 'Prefer to use another device?' : 'Prefer to use your phone?'}
					</p>
					<p class="text-muted-foreground text-xs">
						{touch
							? 'Get a QR code or a link to open on another device. No sign-in is necessary.'
							: 'Scan a QR code and take the photos there. No sign-in is necessary.'}
					</p>
				</div>
				<Button
					variant="outline"
					size="sm"
					class="w-full sm:w-auto"
					disabled={phone.busy}
					onclick={phone.start}
				>
					{phone.busy ? 'Preparing the link…' : phoneLabel}
				</Button>
			</div>
			{#if phone.error}
				<p class="text-destructive text-xs" role="alert">{phone.error}</p>
			{/if}
		{/if}
		{#if capture.attempt > 1}
			<p class="text-muted-foreground text-xs font-medium tracking-wide uppercase">
				{attemptLabel(capture.attempt)}
			</p>
		{/if}

		{#if stage === 'type'}
			<div class="space-y-4">
				<div class="space-y-1">
					<p class="font-medium">Choose your photo ID</p>
					<p class="text-muted-foreground text-sm">
						Please use an original, in date, document. You can take a photo of it, or upload a photo
						or PDF scan.
					</p>
				</div>
				<div role="radiogroup" aria-label="Photo ID" class="grid gap-2 sm:grid-cols-3">
					{#each IDENTITY_DOCUMENT_OPTIONS as option (option.value)}
						{@const chosen = documentType === option.value}
						{@const Icon = DOCUMENT_ICONS[option.value]}
						<button
							type="button"
							role="radio"
							aria-checked={chosen}
							aria-label={option.label}
							class={cn(
								'flex items-center gap-3 rounded-lg border p-3 text-left transition-colors sm:flex-col sm:items-start sm:gap-2 sm:p-4',
								'focus-visible:ring-ring/50 outline-none focus-visible:ring-[3px]',
								chosen ? 'border-primary bg-primary/5' : 'hover:bg-muted/50'
							)}
							onclick={() => selectType(option.value)}
						>
							<span
								class={cn(
									'flex size-9 shrink-0 items-center justify-center rounded-md',
									chosen ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'
								)}
							>
								<Icon class="size-5" />
							</span>
							<span class="min-w-0">
								<span class="block text-sm font-medium">{option.label}</span>
								<span class="text-muted-foreground block text-xs">{option.sides}</span>
							</span>
						</button>
					{/each}
				</div>
				<div class="flex flex-wrap items-center justify-between gap-3">
					<p class="text-muted-foreground text-xs">Next: your ID, then a selfie.</p>
					<Button disabled={!documentType} onclick={begin}>Continue</Button>
				</div>
			</div>
		{:else if stage === 'shot' && shot}
			<div class="space-y-3">
				<div class="flex items-center justify-between gap-2">
					<p class="text-muted-foreground text-xs font-medium tracking-wide uppercase">
						Photo {shotIndex + 1} of {shots.length}
					</p>
					{@render changeTypeButton()}
				</div>
				{#key `${shot.key}:${shotIndex}`}
					<!-- A document offers the camera and an upload as equal choices; the face opens the
					     camera first. -->
					<CameraCapture
						bind:cameraOpen
						mode={shot.mode}
						startWith={shot.mode === 'document' ? 'choice' : 'camera'}
						label={shot.label}
						hint={shot.hint}
						fileName={shot.fileName}
						check={checkFor(shot)}
						oncapture={onShot}
					/>
				{/key}
				<!-- One Back at a time: while the document camera is open, its own Back (to the choices)
				     is the one shown. From the first photo the way back is "Change ID type", above. -->
				{#if !(shot.mode === 'document' && cameraOpen) && (returnToReview || shotIndex > 0)}
					<Button variant="ghost" size="sm" onclick={back}>Back</Button>
				{/if}
			</div>
		{:else if stage === 'review'}
			<div class="space-y-4">
				<div class="flex items-center justify-between gap-2">
					<p class="text-sm font-medium">Check your photos</p>
					{@render changeTypeButton()}
				</div>
				<!-- Two across on a phone, so the likeness box and Submit stay close under the photos. -->
				<ul class="grid grid-cols-2 gap-3 sm:grid-cols-3">
					{#each shots as s, i (s.key)}
						{@const file = photos[s.key]}
						<li class="space-y-2">
							<div
								class="bg-muted flex aspect-[4/3] items-center justify-center overflow-hidden rounded"
							>
								{#if file && isPdfFile(file)}
									<CapturedFileCard {file} class="mx-3 max-w-[calc(100%-1.5rem)]" />
								{:else if thumbnails[s.key]}
									<img src={thumbnails[s.key]} alt={s.label} class="size-full object-contain" />
								{/if}
							</div>
							<div class="flex items-center justify-between gap-2">
								<p class="text-muted-foreground text-xs">{s.label}</p>
								<Button
									variant="ghost"
									size="sm"
									disabled={sending}
									aria-label="Change: {s.label}"
									onclick={() => retakeOne(i)}
								>
									Change
								</Button>
							</div>
						</li>
					{/each}
				</ul>
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox
						checked={likeness}
						disabled={sending}
						onCheckedChange={(v) => (likeness = v === true)}
					/>
					<span>This is a true likeness of me.</span>
				</label>
				{#if error}
					<p class="text-destructive text-xs" role="alert">{error}</p>
				{/if}
				<div class="flex flex-wrap justify-end gap-2">
					<Button disabled={!canSubmit} onclick={submit}>
						{sending ? 'Sending…' : 'Submit photos'}
					</Button>
				</div>
			</div>
		{/if}
	{/if}
</div>
