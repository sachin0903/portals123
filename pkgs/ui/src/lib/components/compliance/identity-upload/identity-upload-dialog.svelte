<script lang="ts">
	// Upload new identity photos for a client, on their behalf: the client sent a new document
	// another way, theirs expired, or their photos were poor and they sent better ones. A new
	// identity check starts from them. A part left out is kept: the document or selfie on file is
	// used again. Each photo goes through the camera component the client portal uses, so an upload
	// is brought inside the identity check's size window, a HEIC photo is read and a PDF is held to
	// one page. The files live here, not in an <input>, so the form is posted by hand to the page's
	// form action.
	import { onMount, untrack } from 'svelte';
	import { applyAction, deserialize } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import InfoIcon from '@lucide/svelte/icons/info';
	import RotateCcwIcon from '@lucide/svelte/icons/rotate-ccw';
	import { Alert, AlertDescription } from '../../ui/alert/index.js';
	import { Button } from '../../ui/button/index.js';
	import { CameraCapture, CapturedFileCard, isPdfFile } from '../../ui/camera-capture/index.js';
	import { Checkbox } from '../../ui/checkbox/index.js';
	import { Combobox, type ComboboxOption } from '../../ui/combobox/index.js';
	import * as Dialog from '../../ui/dialog/index.js';
	import { Label } from '../../ui/label/index.js';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import { Textarea } from '../../ui/textarea/index.js';
	import { countryName } from '../../../countries.js';
	import {
		IDENTITY_DOCUMENT_TYPES,
		documentTypeLabel,
		uploadSlots,
		type IdentityDocumentType,
		type IdentityPhotoField,
		type IdentityUpload
	} from './identity-upload.js';

	let {
		clientName,
		upload,
		onclose,
		action = '?/uploadPhotos',
		noteLabel,
		notePlaceholder,
		countriesUrl = '/pickers/countries'
	}: {
		clientName: string;
		upload: IdentityUpload;
		onclose: () => void;
		/** The page's form action that forwards the upload to the API. */
		action?: string;
		/** Who reads the note, in the field's label: it is never shown to the client. */
		noteLabel: string;
		notePlaceholder: string;
		/** The portal's same-origin countries picker: `{ code, name }[]`. */
		countriesUrl?: string;
	} = $props();

	/** The longest note the API takes. */
	const NOTE_MAX = 5000;

	// A part with nothing on file has to be sent: it starts ticked and stays ticked.
	const keptDocument = $derived(upload.document);
	const canKeepSelfie = $derived(upload.selfie);
	let sendDocument = $state(untrack(() => upload.document === null));
	let sendSelfie = $state(untrack(() => !upload.selfie));

	let documentType = $state('');
	let issuingCountry = $state(untrack(() => upload.issuing_country ?? ''));
	let photos = $state.raw<Partial<Record<IdentityPhotoField, File>>>({});
	let note = $state('');
	let sending = $state(false);
	let error = $state('');

	const documentTypeItems = IDENTITY_DOCUMENT_TYPES.map((d) => ({
		value: d.value,
		label: d.label
	}));

	/** The photos to send, for the parts chosen. A document asks for its sides once its type is
	 *  chosen. */
	const slots = $derived(
		uploadSlots(
			sendDocument && documentType ? (documentType as IdentityDocumentType) : null,
			sendSelfie
		)
	);
	const ready = $derived(
		(sendDocument || sendSelfie) &&
			(!sendDocument || !!documentType) &&
			slots.every((slot) => photos[slot.field]) &&
			!!note.trim() &&
			!sending
	);

	function chooseType(value: string): void {
		if (value === documentType) return;
		documentType = value;
		// The sides of one document are not the sides of another. The selfie stays.
		photos = photos.idv_selfie ? { idv_selfie: photos.idv_selfie } : {};
	}

	function take(field: IdentityPhotoField, file: File): void {
		photos = { ...photos, [field]: file };
	}

	function replace(field: IdentityPhotoField): void {
		const rest = { ...photos };
		delete rest[field];
		photos = rest;
	}

	// Previews of the chosen photos, released when a photo changes or the dialog closes. A PDF scan
	// shows as a file card instead.
	let previews = $state<Partial<Record<IdentityPhotoField, string>>>({});
	$effect(() => {
		const urls: Partial<Record<IdentityPhotoField, string>> = {};
		for (const [field, file] of Object.entries(photos) as [IdentityPhotoField, File][]) {
			if (file && !isPdfFile(file)) urls[field] = URL.createObjectURL(file);
		}
		previews = urls;
		return () => {
			for (const url of Object.values(urls)) if (url) URL.revokeObjectURL(url);
		};
	});

	// Every country, from the same-origin picker. Until it answers, the default is the one choice,
	// so the box shows its name rather than its code.
	let countries = $state<ComboboxOption[]>([]);
	const countryOptions = $derived(
		countries.length
			? countries
			: issuingCountry
				? [{ value: issuingCountry, label: countryName(issuingCountry) }]
				: []
	);
	let loadingCountries = false;

	async function loadCountries(): Promise<void> {
		if (countries.length || loadingCountries) return;
		loadingCountries = true;
		try {
			const res = await fetch(countriesUrl);
			if (!res.ok) return;
			const rows = (await res.json()) as { code: string; name: string }[];
			countries = rows.map((row) => ({ value: row.code, label: row.name }));
		} catch {
			// The default country stays to choose; the API falls back to it when none is sent.
		} finally {
			loadingCountries = false;
		}
	}

	onMount(() => void loadCountries());

	function close(): void {
		if (!sending) onclose();
	}

	async function send(): Promise<void> {
		if (!ready) return;
		sending = true;
		error = '';
		const body = new FormData();
		if (sendDocument) {
			body.append('document_type', documentType);
			if (issuingCountry) body.append('issuing_country', issuingCountry);
		}
		body.append('comment', note.trim());
		for (const slot of slots) {
			const file = photos[slot.field];
			if (file) body.append(slot.field, file, file.name);
		}
		try {
			const res = await fetch(action, { method: 'POST', body });
			const result = deserialize(await res.text());
			if (result.type === 'success') {
				toast.success('Photos uploaded. A new identity check has started.');
				sending = false;
				onclose();
				await invalidateAll();
			} else if (result.type === 'failure') {
				error = String(result.data?.error ?? 'The photos could not be uploaded.');
			} else {
				await applyAction(result);
			}
		} catch {
			error = 'The photos could not be uploaded. Try again.';
		} finally {
			sending = false;
		}
	}
</script>

<Dialog.Root open onOpenChange={(open) => !open && close()}>
	<!-- The fields scroll; the footer stays below them, outside the scroller. -->
	<Dialog.Content class="flex max-h-[90svh] flex-col sm:max-w-xl">
		<Dialog.Header>
			<Dialog.Title>Upload new photos</Dialog.Title>
			<Dialog.Description>
				Photos {clientName} sent another way, such as by email. A new identity check starts from them.
			</Dialog.Description>
		</Dialog.Header>

		<div class="-mx-1 min-h-0 flex-1 space-y-4 overflow-y-auto px-1 py-1">
			{#if upload.journey}
				<Alert variant="info">
					<InfoIcon />
					<AlertDescription>
						The client's onboarding waits on this check. These photos answer it, so the client is
						asked for nothing.
					</AlertDescription>
				</Alert>
			{/if}

			<div class="space-y-2">
				<Label>Which photos</Label>
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox
						checked={sendDocument}
						disabled={sending || (sendDocument && !keptDocument)}
						onCheckedChange={(v) => (sendDocument = v === true)}
					/>
					<span>
						Document
						<span class="text-muted-foreground block text-xs">
							{keptDocument
								? `Leave this out to keep the ${documentTypeLabel(keptDocument.document_type).toLowerCase()} on file.`
								: 'No document is on file for the client, so one is needed.'}
						</span>
					</span>
				</label>
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox
						checked={sendSelfie}
						disabled={sending || (sendSelfie && !canKeepSelfie)}
						onCheckedChange={(v) => (sendSelfie = v === true)}
					/>
					<span>
						Selfie
						<span class="text-muted-foreground block text-xs">
							{canKeepSelfie
								? 'Leave this out to keep the selfie on file.'
								: 'No selfie is on file for the client, so one is needed.'}
						</span>
					</span>
				</label>
			</div>

			{#if sendDocument}
				<div class="grid gap-4 sm:grid-cols-2">
					<div class="space-y-2">
						<Label for="upload-document-type">Document type</Label>
						<SelectMenu
							id="upload-document-type"
							items={documentTypeItems}
							value={documentType}
							onValueChange={chooseType}
							placeholder="Choose the document…"
							disabled={sending}
							required
						/>
					</div>
					<div class="space-y-2">
						<Label for="upload-issuing-country">Issuing country</Label>
						<Combobox
							id="upload-issuing-country"
							bind:value={issuingCountry}
							options={countryOptions}
							placeholder="Choose a country…"
							emptyText="No country matches."
							disabled={sending}
							onOpen={() => void loadCountries()}
						/>
					</div>
				</div>
			{/if}

			{#each slots as slot (slot.field)}
				{@const file = photos[slot.field]}
				{#if file}
					<div class="space-y-2">
						<p class="text-sm font-medium">{slot.label}</p>
						<div class="flex items-center gap-3">
							{#if isPdfFile(file)}
								<CapturedFileCard {file} class="flex-1" />
							{:else if previews[slot.field]}
								<img
									src={previews[slot.field]}
									alt="{slot.label}: the chosen photo"
									class="bg-muted h-28 max-w-[65%] rounded-lg border object-contain"
								/>
							{/if}
							<Button
								variant="outline"
								size="sm"
								class="ml-auto"
								disabled={sending}
								aria-label="Replace: {slot.label}"
								onclick={() => replace(slot.field)}
							>
								<RotateCcwIcon /> Replace
							</Button>
						</div>
					</div>
				{:else}
					<!-- Upload first: the uploader mostly has the client's files, not the client. The
					     camera is a link away. -->
					<CameraCapture
						mode={slot.mode}
						label={slot.label}
						fileName={slot.fileName}
						preferUpload
						disabled={sending}
						oncapture={(chosen) => take(slot.field, chosen)}
					/>
				{/if}
			{/each}

			<div class="space-y-2">
				<Label for="upload-note">{noteLabel}</Label>
				<Textarea
					id="upload-note"
					bind:value={note}
					rows={3}
					maxlength={NOTE_MAX}
					disabled={sending}
					placeholder={notePlaceholder}
				/>
			</div>

			{#if error}
				<p class="text-destructive text-xs" role="alert">{error}</p>
			{/if}
		</div>

		<Dialog.Footer>
			<Button type="button" variant="outline" disabled={sending} onclick={close}>Cancel</Button>
			<Button type="button" disabled={!ready} onclick={send}>
				{sending ? 'Uploading…' : 'Upload and check'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
