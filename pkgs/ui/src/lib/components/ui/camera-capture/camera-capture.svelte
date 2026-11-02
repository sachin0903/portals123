<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import CameraIcon from '@lucide/svelte/icons/camera';
	import CheckIcon from '@lucide/svelte/icons/check';
	import CircleAlertIcon from '@lucide/svelte/icons/circle-alert';
	import FileUpIcon from '@lucide/svelte/icons/file-up';
	import ImageUpIcon from '@lucide/svelte/icons/image-up';
	import RotateCcwIcon from '@lucide/svelte/icons/rotate-ccw';
	import { Alert, AlertDescription, AlertTitle } from '../alert/index.js';
	import { Button } from '../button/index.js';
	import { ConfirmDialog } from '../confirm-dialog/index.js';
	import { cn } from '../../../utils.js';
	import CapturedFileCard from './captured-file-card.svelte';
	import { PLACEMENT_HINT, watchFace, type FacePlacement } from './autocapture.js';
	import {
		cameraConstraints,
		cameraProblem,
		captureFrame,
		captureHint,
		frameAspect,
		liveCameraSupported,
		nativeCapture,
		isPdfFile,
		MAX_CHOSEN_BYTES,
		EMPTY_ERROR,
		normaliseUpload,
		pdfPagesError,
		preparePdf,
		stopStream,
		TOO_LARGE_ERROR,
		sizeProblem,
		tooSmallError,
		uploadAccept,
		uploadLimits,
		within,
		type CameraCaptureMode,
		type CameraCaptureStart,
		type CameraProblem
	} from './capture.js';

	/**
	 * Take ONE photo with the device camera, or upload one where the camera is refused, missing or
	 * not wanted. Live view → Take photo → preview → Retake (Change choice, for an upload) / Use
	 * photo; `oncapture` fires only on the accept button. A photo is always a JPEG `File` inside the
	 * check's size window (an upload is re-encoded, see `normaliseUpload`). In `document` mode a PDF
	 * scan may be uploaded instead: a one-page PDF inside the window is passed on as it is, named
	 * `<fileName>.pdf`, and previewed as a file card; one over 4 MB is sent as that page, a JPEG; a
	 * PDF of more than one page is refused (`preparePdf`).
	 *
	 * A file may be dropped on the choices or the upload box as well as chosen. A file under 34 KB or
	 * over 20 MB is refused before it is read.
	 *
	 * In `face` mode the photo also takes itself, once one face sits in the oval and holds still
	 * (`watchFace`); the person is told how to move until it does. Take photo stays, for when no
	 * face is found or the face finder cannot load.
	 *
	 * Nothing is mirrored, the selfie's live view included: the person sees the photo that is
	 * sent, facing the way their photo ID shows them. A mirrored live view made the photo flip the
	 * moment it was taken, or else face the other way to the ID beside it.
	 *
	 * The stream is released the moment a frame is taken and on unmount, so the camera light never
	 * stays on behind a preview. Key the component (`{#key}`) to take a second, different photo.
	 */
	let {
		mode,
		label,
		hint,
		fileName,
		quality = 0.92,
		startWith = 'camera',
		preferUpload = false,
		disabled = false,
		useLabel = 'Use photo',
		check,
		cameraOpen = $bindable(false),
		oncapture,
		class: className
	}: {
		/** `document`: rear camera, card-shaped guide, a photo or a PDF scan. `face`: front camera,
		 *  oval guide, photos only. */
		mode: CameraCaptureMode;
		/** What this photo is, e.g. "Front of your passport". The group's accessible name. */
		label: string;
		/** One line of guidance under the label. */
		hint?: string;
		/** The captured file's name, without the extension. Defaults to `selfie` / `document`. */
		fileName?: string;
		/** JPEG quality, 0–1, of the first encoding. A photo outside the size window is
		 *  re-encoded. */
		quality?: number;
		/** `camera` (the default) opens the camera at once. `choice` first offers "Take a photo"
		 *  and an upload as two equal choices; the camera starts only when it is chosen. */
		startWith?: CameraCaptureStart;
		/** Start on the upload fallback instead of opening the camera. */
		preferUpload?: boolean;
		disabled?: boolean;
		/** The accept button's label for a photo. A PDF reads "Use file". */
		useLabel?: string;
		/** A rough look at a photo (never a PDF) once it is previewed: what to warn the person
		 *  about, e.g. `checkDocumentPhoto`. Use photo waits for it (never longer than
		 *  `CHECK_TIMEOUT_MS`), and a photo it warned about is used once the person confirms. It
		 *  never refuses one, because a check that guesses wrong must not stop a good photo. */
		check?: (photo: File) => Promise<readonly string[]>;
		/** Whether the camera has the screen (starting or live). Bind it to hide a parent's own
		 *  Back while the camera's Back is showing. */
		cameraOpen?: boolean;
		oncapture: (file: File) => void;
		class?: string;
	} = $props();

	type Phase = 'choice' | 'starting' | 'live' | 'preview' | 'upload';

	let phase = $state<Phase>(
		untrack(() => (preferUpload ? 'upload' : startWith === 'choice' ? 'choice' : 'starting'))
	);
	let problem = $state<CameraProblem | null>(null);
	let stream = $state.raw<MediaStream | null>(null);
	let video = $state<HTMLVideoElement | null>(null);
	let fileInput = $state<HTMLInputElement | null>(null);
	/** Opens the device's own camera when a live view cannot (a plain http link on a phone). */
	let cameraInput = $state<HTMLInputElement | null>(null);
	let photo = $state.raw<File | null>(null);
	let photoUrl = $state<string | null>(null);
	/** Where the previewed file came from, so Retake goes back to the same place. */
	let source = $state<'camera' | 'upload'>('camera');
	let busy = $state(false);
	let error = $state('');
	/** Where the face is against the oval while a selfie is live. Null when no face finder is
	 *  watching: not a selfie, not live yet, or the finder did not load. */
	let placement = $state<FacePlacement | null>(null);
	/** A file is being dragged over the component. */
	let dragging = $state(false);
	/** What `check` warned about for the previewed photo, and whether it is still looking. */
	let warnings = $state<readonly string[]>([]);
	let checking = $state(false);
	/** The "use it anyway?" question is open for a photo the check warned about. */
	let confirming = $state(false);
	/** The longest a check may take before the preview stops saying it is checking. It loads two
	 *  WebAssembly engines the first time, and must never leave the person waiting on one that
	 *  failed. */
	const CHECK_TIMEOUT_MS = 70_000;
	/** Bumped for every previewed photo, so a check that answers after a Retake is dropped. */
	let checkTicket = 0;
	let destroyed = false;
	/** Bumped on every open and every stop, so a `getUserMedia` that resolves late is released
	 *  instead of attached. */
	let request = 0;

	const stem = $derived(fileName ?? (mode === 'face' ? 'selfie' : 'document'));
	const frameClass = $derived(mode === 'face' ? 'aspect-[3/4] max-w-sm' : 'aspect-[4/3] max-w-xl');
	const isDocument = $derived(mode === 'document');
	const photoIsPdf = $derived(photo !== null && isPdfFile(photo));
	/** What the upload is called: a document may be a PDF scan, a face is a photo. */
	const uploadNoun = $derived(isDocument ? 'a photo or PDF' : 'a photo');
	/** A dropped file is taken on the two choices and the upload box. */
	const droppable = $derived(!disabled && !busy && (phase === 'choice' || phase === 'upload'));

	async function startCamera(): Promise<void> {
		error = '';
		const media = typeof navigator === 'undefined' ? undefined : navigator.mediaDevices;
		if (!media?.getUserMedia) {
			problem = 'unavailable';
			phase = 'upload';
			return;
		}
		phase = 'starting';
		const ticket = ++request;
		try {
			const opened = await media.getUserMedia(cameraConstraints(mode));
			if (destroyed || ticket !== request) {
				stopStream(opened);
				return;
			}
			stream = opened;
			problem = null;
			phase = 'live';
		} catch (err) {
			if (destroyed || ticket !== request) return;
			problem = cameraProblem(err);
			phase = 'upload';
		}
	}

	function stopCamera(): void {
		request++;
		stopStream(stream);
		stream = null;
	}

	function clearPhoto(): void {
		if (photoUrl) URL.revokeObjectURL(photoUrl);
		photoUrl = null;
		photo = null;
		checkTicket++;
		warnings = [];
		checking = false;
		confirming = false;
	}

	function showPreview(file: File, from: 'camera' | 'upload'): void {
		clearPhoto();
		photo = file;
		source = from;
		// A PDF is shown as a file card, not drawn.
		photoUrl = isPdfFile(file) ? null : URL.createObjectURL(file);
		phase = 'preview';
		if (check && !isPdfFile(file)) void runCheck(check, file);
	}

	async function runCheck(
		look: (photo: File) => Promise<readonly string[]>,
		file: File
	): Promise<void> {
		const ticket = checkTicket;
		checking = true;
		let found: readonly string[] = [];
		try {
			found = await within(look(file), CHECK_TIMEOUT_MS, []);
		} catch {
			// A check that fails warns about nothing.
		}
		if (destroyed || ticket !== checkTicket) return;
		warnings = found;
		checking = false;
	}

	async function takePhoto(): Promise<void> {
		if (!video || busy) return;
		busy = true;
		error = '';
		try {
			const file = await captureFrame(video, {
				fileName: stem,
				quality,
				aspect: frameAspect(mode)
			});
			if (!file) {
				error = 'The camera is not ready yet. Try again in a moment.';
				return;
			}
			stopCamera();
			showPreview(file, 'camera');
		} finally {
			busy = false;
		}
	}

	/** From the camera back to the two choices, where an upload is already offered. */
	function backToChoice(): void {
		stopCamera();
		error = '';
		phase = 'choice';
	}

	function chooseUpload(): void {
		stopCamera();
		error = '';
		phase = 'upload';
	}

	/** A live view where the browser allows one. Otherwise the device's own camera, which a phone
	 *  still opens from a plain http link. */
	function chooseCamera(): void {
		if (!liveCameraSupported()) {
			cameraInput?.click();
			return;
		}
		void startCamera();
	}

	function openPicker(): void {
		error = '';
		fileInput?.click();
	}

	async function onFileChosen(event: Event): Promise<void> {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		if (file) await takeFile(file);
	}

	/** Bring a chosen or dropped file inside the size window and preview it. */
	async function takeFile(file: File): Promise<void> {
		if (busy) return;
		error = '';
		if (file.size > MAX_CHOSEN_BYTES) {
			error = TOO_LARGE_ERROR;
			return;
		}
		if (file.size === 0) {
			error = EMPTY_ERROR;
			return;
		}
		const pdf = isPdfFile(file);
		if (pdf && !isDocument) {
			error = 'A PDF cannot be used here. Choose a JPG, PNG or HEIC photo.';
			return;
		}
		busy = true;
		try {
			if (pdf) {
				const prepared = await preparePdf(file, { fileName: stem, quality });
				if ('error' in prepared) {
					error =
						prepared.error === 'pages'
							? pdfPagesError(prepared.pages, label)
							: 'That PDF could not be read. Choose a different file.';
					return;
				}
				if (sizeProblem(prepared.file.size) === 'small') {
					error = tooSmallError(mode);
					return;
				}
				showPreview(prepared.file, 'upload');
				return;
			}
			const prepared = await normaliseUpload(file, { fileName: stem, quality });
			if (!prepared) {
				error = isDocument
					? 'That file could not be read. Choose a JPG, PNG or HEIC photo, or a PDF.'
					: 'That file could not be read as a photo. Choose a JPG, PNG or HEIC photo.';
				return;
			}
			// Enlarged as far as it goes and still under the window: the check will not take it.
			if (sizeProblem(prepared.size) === 'small') {
				error = tooSmallError(mode);
				return;
			}
			showPreview(prepared, 'upload');
		} finally {
			busy = false;
		}
	}

	function draggingFiles(event: DragEvent): boolean {
		return event.dataTransfer?.types.includes('Files') ?? false;
	}

	/** A file dragged over the component is always held, so a drop the component does not take (over
	 *  the camera, a preview, or while busy) does not make the browser open the file in place of the
	 *  page. */
	function onDragOver(event: DragEvent): void {
		if (!draggingFiles(event)) return;
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = droppable ? 'copy' : 'none';
		dragging = droppable;
	}

	function onDragLeave(event: DragEvent): void {
		const to = event.relatedTarget;
		if (to instanceof Node && (event.currentTarget as HTMLElement).contains(to)) return;
		dragging = false;
	}

	function onDrop(event: DragEvent): void {
		dragging = false;
		if (!draggingFiles(event)) return;
		event.preventDefault();
		if (!droppable) return;
		const file = event.dataTransfer?.files?.[0];
		if (!file) return;
		void takeFile(file);
	}

	function retake(): void {
		clearPhoto();
		error = '';
		if (source === 'camera') void startCamera();
		else phase = startWith === 'choice' ? 'choice' : 'upload';
	}

	/** Use photo: at once for a photo the check had nothing to say about, after a confirm for one
	 *  it warned about. */
	function usePhoto(): void {
		if (!photo || disabled || checking) return;
		if (warnings.length) {
			confirming = true;
			return;
		}
		accept();
	}

	function accept(): void {
		if (photo && !disabled) oncapture(photo);
	}

	// Attach the stream to whichever <video> is on screen.
	$effect(() => {
		const element = video;
		if (!element) return;
		element.srcObject = stream;
		if (stream) element.play()?.catch(() => {});
	});

	$effect(() => {
		cameraOpen = phase === 'starting' || phase === 'live';
		return () => {
			cameraOpen = false;
		};
	});

	// A live selfie takes itself once the face is placed and still.
	$effect(() => {
		const element = video;
		if (mode !== 'face' || phase !== 'live' || disabled || !element) return;
		const stop = watchFace(element, {
			aspect: frameAspect(mode),
			onplacement: (next) => (placement = next),
			oncapture: () => void takePhoto()
		});
		return () => {
			stop();
			placement = null;
		};
	});

	onMount(() => {
		if (phase === 'starting') {
			if (liveCameraSupported()) void startCamera();
			else phase = 'choice';
		}
		return () => {
			destroyed = true;
			stopCamera();
			if (photoUrl) URL.revokeObjectURL(photoUrl);
		};
	});
</script>

<div
	role="group"
	aria-label={label}
	data-dragging={dragging || undefined}
	class={cn('space-y-3', className)}
	ondragover={onDragOver}
	ondragleave={onDragLeave}
	ondrop={onDrop}
>
	<div>
		<p class="text-sm font-medium">{label}</p>
		{#if hint}
			<p class="text-muted-foreground text-sm">{hint}</p>
		{/if}
	</div>

	<!-- The one file picker, opened by the upload buttons below. Out of the tab order: those
	     buttons are the controls. -->
	<input
		bind:this={fileInput}
		type="file"
		accept={uploadAccept(mode)}
		capture={captureHint(mode)}
		class="sr-only"
		tabindex="-1"
		aria-label="{label}: choose {uploadNoun}"
		{disabled}
		onchange={onFileChosen}
	/>
	<!-- The device camera, for a page that cannot open a live view. A document's upload picker
	     above offers files as well; this one goes straight to the camera. -->
	<input
		bind:this={cameraInput}
		type="file"
		accept="image/*"
		capture={nativeCapture(mode)}
		class="sr-only"
		tabindex="-1"
		data-slot="native-camera"
		aria-label="{label}: take a photo"
		{disabled}
		onchange={onFileChosen}
	/>

	{#if phase === 'choice'}
		<div
			class={cn(
				'grid gap-3 rounded-lg sm:grid-cols-2',
				dragging && 'ring-ring ring-offset-background ring-2 ring-offset-2'
			)}
		>
			<Button
				variant="outline"
				class="h-auto flex-col gap-2 py-6"
				disabled={disabled || busy}
				onclick={chooseCamera}
			>
				<CameraIcon class="size-6" />
				Take a photo
			</Button>
			<Button
				variant="outline"
				class="h-auto flex-col gap-2 py-6"
				disabled={disabled || busy}
				onclick={openPicker}
			>
				<FileUpIcon class="size-6" />
				{busy ? 'Reading the file…' : `Upload ${uploadNoun}`}
			</Button>
		</div>
		<p class="text-muted-foreground text-center text-xs">
			{dragging
				? 'Drop the file to use it.'
				: `${uploadLimits(mode)} You can also drag a file here.`}
		</p>
	{:else if phase === 'starting' || phase === 'live'}
		<div
			class={cn('relative mx-auto w-full overflow-hidden rounded-lg bg-(--hero-base)', frameClass)}
		>
			<!-- muted + playsinline: iOS plays an inline camera feed only when both are set. -->
			<video
				bind:this={video}
				autoplay
				muted
				playsinline
				disablepictureinpicture
				aria-label="Camera view"
				class="size-full object-cover"
			></video>
			<!-- A clear layer over the feed. A browser puts its own buttons over a video the pointer is
			     on (Edge: picture in picture, Enhance), and no attribute turns all of them off. -->
			<div aria-hidden="true" data-slot="camera-shield" class="absolute inset-0"></div>
			<!-- The guide frame. The huge spread shadow dims everything outside it; the container's
			     overflow-hidden clips it to the viewfinder. -->
			<div
				aria-hidden="true"
				data-slot="camera-guide"
				data-shape={mode === 'face' ? 'oval' : 'card'}
				data-placed={placement === 'placed' || undefined}
				class={cn(
					'pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 border-2 shadow-[0_0_0_9999px_color-mix(in_oklch,var(--hero-base)_55%,transparent)] transition-colors',
					placement === 'placed' ? 'border-success border-4' : 'border-(--hero-foreground)',
					mode === 'face'
						? 'aspect-[3/4] w-[62%] rounded-[50%]'
						: 'aspect-[1.586] w-[86%] rounded-xl'
				)}
			></div>
			{#if phase === 'starting'}
				<p
					role="status"
					class="absolute inset-x-0 bottom-3 text-center text-sm text-(--hero-foreground)"
				>
					Starting the camera…
				</p>
			{:else if placement}
				<p
					role="status"
					data-slot="camera-placement"
					class="absolute inset-x-0 bottom-3 px-3 text-center text-sm font-medium text-(--hero-foreground)"
				>
					{PLACEMENT_HINT[placement]}
				</p>
			{/if}
		</div>
		<div class="flex flex-wrap items-center justify-between gap-2">
			{#if startWith === 'choice'}
				<!-- The choices already offered an upload: one way back to them, not a second upload. -->
				<Button variant="ghost" size="sm" {disabled} onclick={backToChoice}>Back</Button>
			{:else}
				<Button variant="ghost" size="sm" {disabled} onclick={chooseUpload}>
					<ImageUpIcon /> Upload {uploadNoun} instead
				</Button>
			{/if}
			<Button disabled={disabled || busy || phase !== 'live'} onclick={takePhoto}>
				<CameraIcon /> Take photo
			</Button>
		</div>
	{:else if phase === 'preview' && photo}
		{#if photoIsPdf}
			<CapturedFileCard file={photo} class="mx-auto max-w-xl" />
		{:else if photoUrl}
			<div class={cn('bg-muted mx-auto w-full overflow-hidden rounded-lg', frameClass)}>
				<img src={photoUrl} alt="{label}: your photo" class="size-full object-contain" />
			</div>
		{/if}
		{#if checking}
			<p role="status" class="text-muted-foreground text-center text-xs">Checking the photo…</p>
		{:else if warnings.length}
			<Alert variant="warning" data-slot="camera-check">
				<CircleAlertIcon />
				<AlertTitle>Check this photo</AlertTitle>
				<AlertDescription>
					{#if warnings.length === 1}
						{warnings[0]}
					{:else}
						<ul class="list-disc space-y-1 pl-4">
							{#each warnings as warning (warning)}
								<li>{warning}</li>
							{/each}
						</ul>
					{/if}
				</AlertDescription>
			</Alert>
		{/if}
		<div class="flex flex-wrap items-center justify-end gap-2">
			<Button variant="outline" {disabled} onclick={retake}>
				<RotateCcwIcon />
				{source === 'camera' ? 'Retake' : 'Change choice'}
			</Button>
			<Button disabled={disabled || checking} onclick={usePhoto}>
				<CheckIcon />
				{photoIsPdf ? 'Use file' : useLabel}
			</Button>
		</div>
	{:else}
		{#if problem === 'denied'}
			<Alert variant="warning">
				<CircleAlertIcon />
				<AlertTitle>Camera access is blocked</AlertTitle>
				<AlertDescription>
					Allow camera access in your browser settings, or upload {uploadNoun} instead.
				</AlertDescription>
			</Alert>
		{:else if problem === 'unavailable'}
			<Alert variant="info">
				<CircleAlertIcon />
				<AlertTitle>No camera available</AlertTitle>
				<AlertDescription>
					{isDocument
						? 'Upload a photo or PDF instead.'
						: 'Upload a photo instead. On a phone, this opens the camera.'}
				</AlertDescription>
			</Alert>
		{/if}
		<div
			class={cn(
				'flex flex-col items-center gap-3 rounded-lg border border-dashed px-4 py-8 text-center transition-colors',
				dragging && 'border-ring bg-muted/60'
			)}
		>
			<ImageUpIcon class="text-muted-foreground size-6" />
			<Button disabled={disabled || busy} onclick={openPicker}>
				{busy ? 'Reading the file…' : `Choose ${uploadNoun}`}
			</Button>
			<p class="text-muted-foreground text-xs">
				{dragging ? 'Drop the file to use it.' : `Or drag it here. ${uploadLimits(mode)}`}
			</p>
			{#if problem !== 'unavailable'}
				<Button variant="link" size="sm" {disabled} onclick={() => void startCamera()}>
					{problem === 'denied' ? 'Try the camera again' : 'Use the camera instead'}
				</Button>
			{/if}
		</div>
	{/if}

	{#if error}
		<p class="text-destructive text-xs" role="alert">{error}</p>
	{/if}
	<ConfirmDialog
		bind:open={confirming}
		title="Use this photo anyway?"
		description="It may not be the right photo:"
		confirmLabel="Use it anyway"
		cancelLabel="Go back"
		onconfirm={accept}
	>
		<ul
			class="text-muted-foreground list-disc space-y-1 pl-5 text-sm"
			data-slot="camera-check-confirm"
		>
			{#each warnings as warning (warning)}
				<li>{warning}</li>
			{/each}
		</ul>
	</ConfirmDialog>
</div>
