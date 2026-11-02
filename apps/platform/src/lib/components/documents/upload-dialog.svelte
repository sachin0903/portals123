<script lang="ts">
	import {
		Button,
		Dialog,
		FileDropzone,
		Input,
		Label,
		Segmented,
		Select,
		toast
	} from '@graphene/ui';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import UserIcon from '@lucide/svelte/icons/user';
	import { deserialize } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import {
		isTransportStatus,
		uploadTransportError,
		type ClientOption,
		type DocumentTypeOption
	} from '@graphene/ui/book/documents';

	let {
		open = $bindable(false),
		presetFiles = null,
		clients = null,
		documentTypes = [],
		forClient = null
	}: {
		open?: boolean;
		/** Files dropped onto the page (drag-and-drop upload) — pre-fills the dropzone on open. */
		presetFiles?: File[] | null;
		/** Clients for the "who is this for?" / supporting-file client pickers. Passing this also
		    enables the Document / Supporting file switch; omit for a document-only dialog. */
		clients?: ClientOption[] | null;
		/** The tenant's active document-type tags (open-ended registry) — the picker's suggestions.
		    You can also type a brand-new one, which the API registers on upload. */
		documentTypes?: DocumentTypeOption[];
		/** Opened from a client's page: pre-files the upload under this client (the document subject +
		    the supporting-file contact) and shows a reminder, in place of the free client picker. */
		forClient?: ClientOption | null;
	} = $props();

	let files = $state<File[]>([]);
	// What's being uploaded: a library document, or a supporting client file (passport, ID…).
	let kind = $state<'document' | 'file'>('document');
	// Open-ended document type. `docType` is the chosen registry slug; when `addingType`, the type is
	// the free-text `customType` (a brand-new tag the API registers). Required for a document, and
	// never defaulted (no silent 'other') — the adviser picks or creates a specific type.
	let docType = $state('');
	let customType = $state('');
	let addingType = $state(false);
	let classifying = $state(false);
	let autoNote = $state<string | null>(null);
	// Supporting-file attachment type. Starts unselected; auto-detected on file choose (see below),
	// else the adviser picks — required to upload a supporting file (never a silent default).
	let attachmentType = $state('');
	let contactId = $state('');
	// The client this document is *made for* (0..1; filing only — not shared until sent). Matches
	// template generation's intended_contact. Fixed to `forClient` when opened from a client's page.
	let subjectId = $state('');
	// Whether the user has manually chosen a type since the current file was selected — auto-classify
	// must not silently overwrite a manual pick. A plain flag (not $state) so the auto $effect below
	// doesn't take a dependency on it and re-run.
	let typeTouched = false;
	let rejected = $state<string | null>(null);
	let uploading = $state(false);
	let error = $state<string | null>(null);

	// Document types vs supporting-file types come from the SAME registry (documents.document_types),
	// split by kind: the Document tab offers document types, the Supporting-file tab file types.
	const docTypeOptions = $derived(documentTypes.filter((t) => t.kind === 'document'));
	const fileTypeOptions = $derived(documentTypes.filter((t) => t.kind === 'file'));
	// Rough filename → attachment-type guess for supporting files (client-side; the tag registry
	// classify is server-side). A no-match leaves the type unselected for the adviser to choose.
	const ATTACHMENT_HINTS: Record<string, string[]> = {
		passport: ['passport'],
		national_id: ['national', 'nationalid', 'idcard', 'licence', 'license', 'driving', 'dvla'],
		proof_of_address: [
			'proof',
			'address',
			'utility',
			'bill',
			'council',
			'bank',
			'statement',
			'tenancy'
		]
	};
	function classifyAttachment(filename: string): void {
		const tokens = new Set(filename.toLowerCase().match(/[a-z0-9]+/g) ?? []);
		const hinted =
			Object.entries(ATTACHMENT_HINTS).find(([, hints]) => hints.some((h) => tokens.has(h)))?.[0] ??
			'';
		// Only apply the hint if the firm actually has that file type (it may have renamed/removed it).
		const opt = fileTypeOptions.find((t) => t.slug === hinted);
		attachmentType = opt?.slug ?? '';
		autoNote = opt
			? `Detected ${opt.label} — change if needed.`
			: 'Couldn’t auto-classify — choose a type.';
	}

	// What gets submitted as document_type: the typed new tag when adding, else the selected slug.
	const effectiveType = $derived(addingType ? customType.trim() : docType);

	function clientName(c: ClientOption): string {
		return c.name ?? c.email ?? `Client ${c.contact_id}`;
	}

	function onTypeSelect(v: string): void {
		typeTouched = true; // a manual choice — auto-classify must not overwrite it
		autoNote = null;
		if (v === '__new__') {
			addingType = true;
			docType = '';
		} else {
			addingType = false;
			docType = v;
		}
	}

	/** Suggest a type from the first file's name (POST /classify via the page action). A hint, not a
	    save: a known slug is selected; an unknown suggestion pre-fills a new-tag entry. `auto` runs it
	    silently on file-select (and won't clobber a hand-picked type); the button forces a re-run. */
	async function autoDetect(auto = false): Promise<void> {
		if (!files.length || classifying) return;
		if (auto && typeTouched) return;
		classifying = true;
		autoNote = null;
		try {
			const body = new FormData();
			body.append('filename', files[0].name);
			const { result } = await post('/documents?/classify', body);
			const data =
				result?.type === 'success'
					? (result.data as { document_type?: string; label?: string } | undefined)
					: undefined;
			if (data?.document_type) {
				if (docTypeOptions.some((t) => t.slug === data.document_type)) {
					addingType = false;
					docType = data.document_type;
				} else {
					addingType = true;
					customType = data.label ?? data.document_type;
				}
				autoNote = `Detected “${data.label ?? data.document_type}” — change if needed.`;
			} else if (!auto) {
				autoNote = 'Couldn’t detect a type — choose one.';
			}
		} finally {
			classifying = false;
		}
	}

	$effect(() => {
		if (open && presetFiles?.length && files.length === 0) files = presetFiles;
	});

	// Opened from a client's page: pre-file under that client (the document subject + the supporting-
	// file contact). Seeds once per open (reset() clears them on close).
	$effect(() => {
		if (open && forClient) {
			const id = String(forClient.contact_id);
			if (!subjectId) subjectId = id;
			if (!contactId) contactId = id;
		}
	});

	// Auto-classify as soon as a (new) file is chosen — documents against the tag registry (server),
	// supporting files against the attachment-type hints (client). Keyed on kind+filename so it fires
	// once per file/kind, not on every render.
	let lastAutoKey = $state<string | null>(null);
	$effect(() => {
		const fname = files[0]?.name ?? null;
		if (fname) {
			const key = `${kind}:${fname}`;
			if (key !== lastAutoKey) {
				lastAutoKey = key;
				typeTouched = false; // a fresh file/kind — let auto-classify set the type again
				if (kind === 'file') classifyAttachment(fname);
				else void autoDetect(true);
			}
		}
	});

	function reset(): void {
		files = [];
		kind = 'document';
		docType = '';
		customType = '';
		addingType = false;
		classifying = false;
		autoNote = null;
		attachmentType = '';
		contactId = '';
		subjectId = '';
		typeTouched = false;
		lastAutoKey = null;
		rejected = null;
		uploading = false;
		error = null;
	}

	/** Message for a failed/partly-failed ActionResult, or null when everything landed. */
	function errorOf(result: ReturnType<typeof deserialize> | null, fallback: string): string | null {
		if (!result) return fallback;
		if (result.type === 'failure') return String(result.data?.error ?? fallback);
		if (result.type === 'error') return result.error?.message ?? fallback;
		return null;
	}

	type PostedAction =
		| { result: ReturnType<typeof deserialize>; transport: null }
		| { result: null; transport: string };

	/** POST a form action, tolerating a response that never reached it: the node adapter answers 413
	 * from its own request-body ceiling (and a proxy can answer 502/504) with an HTML body, which
	 * `deserialize` throws on — that used to surface as "Unexpected token '<'".
	 *
	 * The same 413 also arrives DESERIALISABLE, as `{type:'error'}` carrying SvelteKit's own bare
	 * "Payload Too Large" — the ceiling bites while the body is being read, so the failure is
	 * SvelteKit's, not our action's, and it is reported in whichever shape the request negotiated.
	 * Both shapes need the friendly line. Only an `error` result is treated this way: `fail(413)` from
	 * our own action is a `failure` carrying the API's message, which names the real limit. */
	async function post(url: string, body: FormData): Promise<PostedAction> {
		const res = await fetch(url, { method: 'POST', body });
		const text = await res.text();
		let result: ReturnType<typeof deserialize>;
		try {
			result = deserialize(text);
		} catch {
			return { result: null, transport: uploadTransportError(res.status) };
		}
		if (result.type === 'error' && isTransportStatus(res.status)) {
			return { result: null, transport: uploadTransportError(res.status) };
		}
		return { result, transport: null };
	}

	/** POST every chosen file to the matching action (document library vs supporting files).
	 *
	 * The dropzone takes a set, so all of it is sent: dropping three files used to upload only the
	 * first and report success. Documents go in ONE request (the endpoint takes a repeated `file`
	 * part and names each rejection back); supporting files take one file per call, so they loop. */
	async function upload(): Promise<void> {
		if (files.length === 0 || uploading) return;
		if (kind === 'document' && !effectiveType) {
			error = 'Choose or add a document type.';
			return;
		}
		if (kind === 'file' && !attachmentType) {
			error = 'Choose an attachment type.';
			return;
		}
		uploading = true;
		error = null;
		try {
			const chosen = [...files];
			const failures: string[] = [];
			let uploaded = 0;
			// The client this upload is filed under: the client whose page it was opened from, else
			// the "who is this for?" pick.
			const subject = forClient ? String(forClient.contact_id) : subjectId;
			if (kind === 'file') {
				for (const file of chosen) {
					const body = new FormData();
					body.append('file', file, file.name);
					body.append('name', file.name);
					body.append('attachment_type', attachmentType);
					if (subject) body.append('contact_id', subject);
					const { result, transport } = await post('/files?/upload', body);
					const message = transport ?? errorOf(result, 'Upload failed.');
					if (message) failures.push(`${file.name}: ${message}`);
					else uploaded += 1;
				}
			} else {
				const body = new FormData();
				for (const file of chosen) body.append('file', file, file.name);
				// A single explicit name only makes sense for a single file; each file in a multi-file
				// drop keeps its own name (the action enforces the same rule).
				if (chosen.length === 1) body.append('name', chosen[0].name);
				body.append('document_type', effectiveType);
				// The client this document is made for (0..1); visibility still needs a send.
				if (subject) body.append('subject_contact_id', subject);
				const { result, transport } = await post('/documents?/upload', body);
				const message = transport ?? errorOf(result, 'Upload failed.');
				if (message) {
					failures.push(message);
				} else if (result && result.type === 'success') {
					// The action reports the API's own counts: what it stored, and every file it refused.
					const data = result.data as { uploaded?: number; failures?: string[] } | undefined;
					uploaded = typeof data?.uploaded === 'number' ? data.uploaded : chosen.length;
					failures.push(...(data?.failures ?? []));
				}
			}
			if (uploaded > 0) await invalidateAll();
			if (uploaded === 0) {
				// Nothing landed — keep the dialog open with the reason rather than closing on a lie.
				error = failures.join('; ') || 'Upload failed.';
				return;
			}
			const noun = kind === 'file' ? 'file' : 'document';
			if (failures.length > 0) {
				toast.error(
					`Uploaded ${uploaded} of ${uploaded + failures.length}. Not uploaded — ${failures.join('; ')}`
				);
			} else if (uploaded === 1) {
				toast.success(kind === 'file' ? 'File uploaded.' : 'Document uploaded.');
			} else {
				toast.success(`Uploaded ${uploaded} ${noun}s.`);
			}
			open = false;
			reset();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Upload failed.';
		} finally {
			uploading = false;
		}
	}
</script>

<Dialog.Root bind:open onOpenChange={(v) => !v && reset()}>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>Upload</Dialog.Title>
			<Dialog.Description>
				{kind === 'file'
					? 'A supporting client file (passport, ID, proof of address) — not a financial document.'
					: "Add a document to your team's library. It isn't shared with a client until you send it."}
			</Dialog.Description>
		</Dialog.Header>

		<div class="space-y-4 py-2">
			{#if clients || forClient}
				<!-- What is this? Document vs supporting file — decides type options + destination. -->
				<Segmented
					options={[
						{ value: 'document', label: 'Document' },
						{ value: 'file', label: 'Supporting file' }
					]}
					value={kind}
					onValueChange={(v) => {
						kind = v as 'document' | 'file';
						// Swapping kind clears the picked file(s) AND the type — a document and a supporting
						// file take different types + destinations, so nothing should carry over.
						files = [];
						docType = '';
						customType = '';
						addingType = false;
						attachmentType = '';
						typeTouched = false;
						rejected = null;
						autoNote = null;
						lastAutoKey = null;
					}}
				/>
			{/if}

			{#if forClient}
				<!-- Reminder: this upload is filed under the client whose page it was opened from. -->
				<div
					class="bg-muted/50 text-muted-foreground flex items-center gap-2 rounded-lg border px-3 py-2 text-sm"
				>
					<UserIcon class="size-4 shrink-0" />
					<span>
						Filing under <span class="text-foreground font-medium">{clientName(forClient)}</span>
					</span>
				</div>
			{/if}

			<!-- `multiple`: the whole set is uploaded (documents in one request, supporting files one
			     per call), so the picker must be allowed to select more than one. -->
			<FileDropzone bind:files multiple rich class="w-full" onreject={(r) => (rejected = r)} />
			{#if rejected}<p class="text-destructive text-sm" role="alert">{rejected}</p>{/if}

			{#if kind === 'file' && (clients || forClient)}
				<div class="grid gap-3 {clients && !forClient ? 'grid-cols-2' : 'grid-cols-1'}">
					<div class="space-y-1.5">
						<div class="flex items-center justify-between">
							<Label for="attachment-type">Type</Label>
							<Button
								variant="ghost"
								size="sm"
								class="h-6 gap-1 px-2 text-xs"
								onclick={() => files[0] && classifyAttachment(files[0].name)}
								disabled={!files.length}
							>
								<SparklesIcon class="text-brand size-3.5" />
								Auto-detect
							</Button>
						</div>
						<Select.Root type="single" bind:value={attachmentType}>
							<Select.Trigger id="attachment-type" class="h-9 w-full">
								<span class={attachmentType ? '' : 'text-muted-foreground'}>
									{fileTypeOptions.find((t) => t.slug === attachmentType)?.label ??
										'Select a type…'}
								</span>
							</Select.Trigger>
							<Select.Content>
								{#each fileTypeOptions as t (t.slug)}
									<Select.Item value={t.slug}>{t.label}</Select.Item>
								{/each}
							</Select.Content>
						</Select.Root>
					</div>
					{#if clients && !forClient}
						<div class="space-y-1.5">
							<Label for="file-client">Client (optional)</Label>
							<Select.Root type="single" bind:value={contactId}>
								<Select.Trigger id="file-client" class="h-9 w-full">
									{@const sel = clients.find((c) => String(c.contact_id) === contactId)}
									{sel ? clientName(sel) : '— None —'}
								</Select.Trigger>
								<Select.Content>
									<Select.Item value="">— None —</Select.Item>
									{#each clients as c (c.contact_id)}
										<Select.Item value={String(c.contact_id)}>{clientName(c)}</Select.Item>
									{/each}
								</Select.Content>
							</Select.Root>
						</div>
					{/if}
				</div>
				{#if autoNote}<p class="text-muted-foreground text-xs">{autoNote}</p>{/if}
			{:else}
				<div class="space-y-1.5">
					<div class="flex items-center justify-between">
						<Label for="upload-type">Type</Label>
						<Button
							variant="ghost"
							size="sm"
							class="h-6 gap-1 px-2 text-xs"
							onclick={() => autoDetect()}
							disabled={!files.length || classifying}
						>
							<SparklesIcon class="text-brand size-3.5" />
							{classifying ? 'Detecting…' : 'Auto-detect'}
						</Button>
					</div>
					{#if addingType}
						<div class="flex gap-2">
							<Input
								bind:value={customType}
								placeholder="New type name…"
								class="h-9"
								aria-label="New document type"
							/>
							<Button
								variant="outline"
								class="h-9 shrink-0"
								onclick={() => {
									addingType = false;
									customType = '';
								}}
							>
								Cancel
							</Button>
						</div>
					{:else}
						<Select.Root type="single" value={docType} onValueChange={onTypeSelect}>
							<Select.Trigger id="upload-type" class="h-9 w-full">
								{docTypeOptions.find((t) => t.slug === docType)?.label ?? 'Choose a type…'}
							</Select.Trigger>
							<Select.Content>
								{#each docTypeOptions as t (t.slug)}
									<Select.Item value={t.slug}>{t.label}</Select.Item>
								{/each}
								<Select.Item value="__new__">＋ New type…</Select.Item>
							</Select.Content>
						</Select.Root>
					{/if}
					{#if autoNote}<p class="text-muted-foreground text-xs">{autoNote}</p>{/if}
				</div>

				{#if clients && !forClient}
					<!-- Which client this document is for. Filing only — not shared until you Send it.
					     (When opened from a client's page the subject is fixed — shown as the reminder
					     above — so this picker is hidden.) -->
					<div class="space-y-1.5">
						<Label for="subject-client"
							>Client <span class="text-muted-foreground font-normal">(optional)</span></Label
						>
						<Select.Root type="single" bind:value={subjectId}>
							<Select.Trigger id="subject-client" class="h-9 w-full">
								{@const sel = clients.find((c) => String(c.contact_id) === subjectId)}
								<span class={subjectId ? '' : 'text-muted-foreground'}>
									{sel ? clientName(sel) : 'Who is this for?'}
								</span>
							</Select.Trigger>
							<Select.Content>
								<Select.Item value="">— None —</Select.Item>
								{#each clients as c (c.contact_id)}
									<Select.Item value={String(c.contact_id)}>{clientName(c)}</Select.Item>
								{/each}
							</Select.Content>
						</Select.Root>
					</div>
				{/if}
			{/if}

			{#if error}<p class="text-destructive text-sm" role="alert">{error}</p>{/if}
		</div>

		<Dialog.Footer>
			<Button
				variant="ghost"
				onclick={() => {
					// Reset explicitly: bits-ui doesn't fire Dialog's onOpenChange when `open` is set
					// programmatically via the binding, so without this the fields (file, type, client)
					// survive the close and reappear pre-filled on the next open.
					reset();
					open = false;
				}}
				disabled={uploading}
			>
				Cancel
			</Button>
			<Button
				disabled={files.length === 0 ||
					uploading ||
					(kind === 'document' && !effectiveType) ||
					(kind === 'file' && !attachmentType)}
				onclick={upload}
			>
				{uploading ? 'Uploading…' : 'Upload'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
