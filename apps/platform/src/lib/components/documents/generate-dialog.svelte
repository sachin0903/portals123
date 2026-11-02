<script lang="ts">
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Button,
		Card,
		CardContent,
		DynamicForm,
		Segmented,
		formatFieldValue,
		DOC_TYPE_META,
		type FormField,
		type Template
	} from '@graphene/ui';
	import FileOutputIcon from '@lucide/svelte/icons/file-output';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import UserIcon from '@lucide/svelte/icons/user';
	import UsersIcon from '@lucide/svelte/icons/users';
	import type { ClientOption } from '@graphene/ui/book/documents';
	import { toast } from '@graphene/ui';
	import RenderProgress from './render-progress.svelte';
	import type { RenderStatus } from './render-progress';

	let {
		template = null,
		fields = [],
		clients = [],
		templateVars = [],
		onClose = undefined,
		onGenerate = undefined,
		onGenerateBatch = undefined
	}: {
		template?: Template | null;
		/** The template's linked input form fields (the real §9.1 questionnaire), from the loader. */
		fields?: FormField[];
		/** The tenant's client-portal users — the real "who is this for" picker. */
		clients?: ClientOption[];
		/** Top-level variables the published template references (from GET /templates/{uid}/variables),
		    used to warn when the collected inputs won't fill them all. */
		templateVars?: string[];
		/** Leave the flow (Cancel / back link). */
		onClose?: (() => void) | undefined;
		/** Enqueue + await the render. Returns an error message on failure, or null on success (the
		    host navigates to the finished document). */
		onGenerate?: (
			input: {
				name: string;
				clientContactId: number | null;
				answers: Record<string, unknown>;
			},
			/**
			 * Called each time the host learns the document's real status. The dialog draws its steps
			 * from this rather than from a timer, so a render worker that is not running cannot look
			 * like a slow render.
			 */
			onProgress?: (status: RenderStatus) => void,
			/**
			 * Handed back so the dialog can offer Cancel. Calling it stops the host's poll and asks
			 * the API to stop the render; it resolves with the outcome to show, because cancelling a
			 * render a worker already holds cannot actually stop it.
			 */
			onCancellable?: (cancel: () => Promise<string>) => void,
			/**
			 * The document generated, but a dataset it binds came back empty. Reported as a warning
			 * rather than an error: the PDF is real and the flow continues to it — but an empty table
			 * with no explanation is the one thing an author cannot debug from the document itself.
			 */
			onEmpty?: (note: string) => void
		) => Promise<string | null>;
		/**
		 * Queue the SAME template for several clients at once. Absent ⇒ the mode is not offered at
		 * all, which is how the admin portal's read-only mounts stay read-only (the injected-callback
		 * rule the book surfaces follow).
		 *
		 * It answers with a report rather than an error string, because a batch legitimately half
		 * succeeds: `queued` is what landed, `rejected` names the clients it could not generate for.
		 * The host does NOT navigate on success — the report is the whole point, and a redirect would
		 * throw it away.
		 */
		onGenerateBatch?: (input: {
			name: string;
			contactIds: number[];
			answers: Record<string, unknown>;
		}) => Promise<
			{ queued: number; rejected: { contactId: number; reason: string }[] } | { error: string }
		>;
	} = $props();

	type GenState = 'idle' | 'review' | 'generating' | 'queued';
	let genState = $state<GenState>('idle');
	/** The server's own phase while a single document renders. */
	let renderStatus = $state<RenderStatus>('submitting');
	let startedAt = $state(0);
	let elapsedMs = $state(0);
	/** Supplied by the host once the document exists and there is something to cancel. */
	let cancelRender = $state<(() => Promise<string>) | null>(null);
	let cancelling = $state(false);

	// One interval for the whole wait, cleared when it ends. `$effect` rather than a timer started
	// in `generate()` so it cannot outlive the component if the host navigates mid-render.
	$effect(() => {
		if (genState !== 'generating' || batchMode) return;
		const tick = setInterval(() => (elapsedMs = Date.now() - startedAt), 250);
		return () => clearInterval(tick);
	});
	let genError = $state<string | null>(null);

	// One client or several. Offered only when the host passes a batch callback AND there is more
	// than one client to choose between — a "several clients" toggle over a book of one is a control
	// that cannot do anything.
	type Audience = 'one' | 'many';
	let audience = $state<Audience>('one');
	const canBatch = $derived(!!onGenerateBatch && clients.length > 1);
	const batchMode = $derived(canBatch && audience === 'many');
	let batchReport = $state<{
		queued: number;
		rejected: { contactId: number; reason: string }[];
	} | null>(null);

	const docTypeMeta = $derived(template ? DOC_TYPE_META[template.documentType] : null);

	// Client picker resolves against the `clients` data source (value = contact id as a string, label =
	// name/email). The id is a string here because the combobox/DynamicForm keys on string ids; it's
	// parsed back to the numeric contact_id on generate.
	const CLIENTS_SOURCE = 'clients';
	const clientSource = $derived(
		clients.map((c) => ({ id: String(c.contact_id), name: c.name ?? c.email ?? 'Client' }))
	);
	const dataSources = $derived({ [CLIENTS_SOURCE]: clientSource });

	// The generation inputs: a "Document details" core (name + client — captured for the render) then
	// the template's linked form fields. The adviser is the logged-in user, recorded server-side.
	// The client field is the ONE thing the two modes disagree about, so it is a swap rather than a
	// second form: the template's own inputs are shared (that is what makes this a batch), and
	// duplicating the form would be two places to keep a field list in step.
	//
	// Separate KEYS (`client` / `clients`), not one key holding either a string or an array: the
	// review summary, the validator and the submit all read it, and a value whose type depends on a
	// mode elsewhere is how one of the three comes to read it wrong.
	const coreFields: FormField[] = [
		{
			uid: 'core:doc_name',
			fieldKey: 'doc_name',
			fieldType: 'text',
			label: 'Document name',
			section: 'Document details',
			description: null,
			required: true,
			conditions: null,
			validation: null,
			options: null,
			sortOrder: 0
		}
	];
	const clientField = $derived<FormField>({
		uid: 'core:client',
		fieldKey: batchMode ? 'clients' : 'client',
		fieldType: batchMode ? 'multiSelect' : 'combobox',
		label: batchMode ? 'Clients' : 'Client',
		section: 'Document details',
		description: batchMode ? 'One document each, with their own details merged in.' : null,
		required: true,
		conditions: null,
		validation: null,
		options: { source: CLIENTS_SOURCE },
		sortOrder: 1
	});
	const allFields = $derived<FormField[]>([...coreFields, clientField, ...fields]);

	// Template variables the collected inputs won't fill: referenced by the template but not provided
	// by a form field, the core fields, the client/context, or a linked dataset. Warned, not blocked.
	const missingVars = $derived.by(() => {
		const provided = new Set<string>([
			...fields.map((f) => f.fieldKey),
			'doc_name',
			'client',
			'clients',
			'document_name',
			'today',
			'answers',
			'client_name',
			'client_email',
			...(template?.linkedDatasets ?? [])
		]);
		return templateVars.filter((v) => !provided.has(v));
	});

	let values = $state<Record<string, unknown>>({});
	let inited = $state(false);
	let formRef = $state<{ validate: () => boolean } | undefined>(undefined);

	const clientName = $derived(clientSource.find((c) => c.id === values['client'])?.name ?? '');
	const docName = $derived(String(values['doc_name'] ?? ''));

	/** The chosen clients as numeric contact ids. Non-numeric and duplicate entries are dropped
	 *  here rather than sent: the API refuses a client it cannot see and de-dupes anyway, but a
	 *  count shown to the operator must match what will actually be generated. */
	const chosenIds = $derived.by(() => {
		const raw = batchMode ? values['clients'] : values['client'];
		const list = Array.isArray(raw) ? raw : raw ? [raw] : [];
		const ids = list.map((v) => Number(v)).filter((n) => Number.isFinite(n) && n > 0);
		return [...new Set(ids)];
	});
	/** A client's name for the skipped-clients report. Falls back to the id only when the client is
	 *  not in the picker's own list — which is exactly the case the API rejects (a contact this
	 *  staffer cannot see), so the id is all there honestly is to show. */
	function clientLabel(contactId: number): string {
		return clientSource.find((c) => c.id === String(contactId))?.name ?? `Client ${contactId}`;
	}

	const audienceSummary = $derived(
		batchMode
			? chosenIds.length === 0
				? '—'
				: `${chosenIds.length} client${chosenIds.length === 1 ? '' : 's'}`
			: clientName || '—'
	);

	// Seed the client default + document name once the template + clients are available.
	$effect(() => {
		if (template && !inited) {
			values = { doc_name: template.name ?? '', client: clientSource[0]?.id ?? '' };
			inited = true;
		}
	});

	// Switching mode clears the other mode's selection. Carrying it over is what would let an
	// operator pick 24 clients, switch to "one client", and generate for whichever of them happened
	// to still be in the single-value field.
	function setAudience(next: Audience): void {
		if (next === audience) return;
		audience = next;
		genError = null;
		values = {
			...values,
			client: next === 'one' ? (clientSource[0]?.id ?? '') : '',
			clients: []
		};
	}

	// Group fields by section for the review summary (DynamicForm handles the fill view).
	const reviewSections = $derived.by(() => {
		const map = new Map<string, FormField[]>();
		for (const f of allFields) {
			const key = f.section || 'Details';
			const arr = map.get(key) ?? [];
			arr.push(f);
			map.set(key, arr);
		}
		return [...map.entries()];
	});

	function reviewValue(f: FormField): string {
		return formatFieldValue({ value: values[f.fieldKey], field: f, dataSources });
	}

	function toReview() {
		if (formRef?.validate()) {
			genState = 'review';
			window.scrollTo({ top: 0, behavior: 'smooth' });
		}
	}

	/** The template's own inputs — the core fields stripped, both client keys included, so a mode
	 *  switch cannot leave the other one in the answers. */
	function templateAnswers(): Record<string, unknown> {
		const answers = { ...values };
		delete answers.doc_name;
		delete answers.client;
		delete answers.clients;
		return answers;
	}

	async function generate() {
		if (batchMode) return generateBatch();
		if (!onGenerate) return;
		genError = null;
		renderStatus = 'submitting';
		startedAt = Date.now();
		elapsedMs = 0;
		cancelRender = null;
		cancelling = false;
		genState = 'generating';
		const clientId = chosenIds[0] ?? null;
		const err = await onGenerate(
			{
				name: docName || template?.name || 'Document',
				clientContactId: clientId,
				answers: templateAnswers()
			},
			(status) => (renderStatus = status),
			(cancel) => (cancelRender = cancel),
			(note) => toast.warning('Generated, with empty data', { description: note })
		);
		if (err) {
			genError = err;
			genState = 'review';
		}
		// On success the host navigates away; this component unmounts.
	}

	async function generateBatch() {
		if (!onGenerateBatch) return;
		genError = null;
		genState = 'generating';
		const result = await onGenerateBatch({
			name: docName || template?.name || 'Document',
			contactIds: chosenIds,
			answers: templateAnswers()
		});
		if ('error' in result) {
			genError = result.error;
			genState = 'review';
			return;
		}
		// Stay put and report. The documents are QUEUED, not rendered — landing the operator on a
		// finished document is the single-client promise and cannot be kept for 24 of them.
		batchReport = result;
		genState = 'queued';
	}
</script>

<div class="space-y-6">
	<a
		href="/documents"
		onclick={(e) => {
			if (onClose) {
				e.preventDefault();
				onClose();
			}
		}}
		class="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
	>
		<ArrowLeftIcon class="size-3.5" /> Documents
	</a>

	{#if !template}
		<div class="mt-16 flex flex-col items-center gap-3 text-center">
			<FileOutputIcon class="text-muted-foreground/40 size-10" />
			<p class="font-medium">No template selected</p>
			<p class="text-muted-foreground text-sm">
				Start from a published template's <em>Generate</em> action.
			</p>
			<Button variant="outline" class="mt-2" onclick={() => onClose?.()}>Back to documents</Button>
		</div>
	{:else}
		<div class="mt-2 flex items-center gap-3">
			<div
				class="flex size-10 shrink-0 items-center justify-center rounded-xl {docTypeMeta?.bgColor}"
			>
				<FileOutputIcon class="size-5 {docTypeMeta?.textColor}" />
			</div>
			<div class="min-w-0">
				<h2 class="text-2xl font-semibold tracking-tight">
					{batchMode ? 'Generate documents' : 'Generate document'}
				</h2>
				<p class="text-muted-foreground truncate text-sm">
					{template.name}{docTypeMeta ? ` · ${docTypeMeta.label}` : ''}
				</p>
			</div>
		</div>

		{#if genState === 'generating' && batchMode}
			<!-- A batch is QUEUED, not rendered, so there is no per-document phase to report — the
			     wait is the one request that enqueues them all. -->
			<div class="flex flex-col items-center gap-4 py-28 text-center">
				<div class="bg-primary/10 flex size-14 items-center justify-center rounded-full">
					<LoaderCircleIcon class="text-primary size-7 animate-spin" />
				</div>
				<div>
					<p class="font-semibold">Queueing your documents…</p>
					<p class="text-muted-foreground mt-1 text-sm">
						Queueing {template.name} for {audienceSummary}.
					</p>
				</div>
			</div>
		{:else if genState === 'generating'}
			<RenderProgress
				status={renderStatus}
				{elapsedMs}
				title="Generating your document"
				subtitle={`${template.name}${clientName ? ` for ${clientName}` : ''}`}
				{cancelling}
				oncancel={cancelRender
					? async () => {
							cancelling = true;
							// The outcome is the message, not a boolean: a render already in flight
							// cannot be stopped, and saying "cancelled" about it would be untrue.
							const outcome = await cancelRender!();
							cancelling = false;
							genError = outcome;
							genState = 'review';
						}
					: undefined}
			/>
		{:else if genState === 'queued' && batchReport}
			<!-- Queued, not rendered. The operator stays here because the report IS the outcome: a
			     batch legitimately half succeeds, and a redirect to the library would throw away the
			     only place the skipped clients are named. -->
			<div class="mx-auto mt-10 max-w-lg space-y-4">
				<div class="flex flex-col items-center gap-3 text-center">
					<div class="bg-primary/10 flex size-14 items-center justify-center rounded-full">
						<FileOutputIcon class="text-primary size-7" />
					</div>
					<div>
						<p class="font-semibold">
							{batchReport.queued} document{batchReport.queued === 1 ? '' : 's'} being prepared
						</p>
						<p class="text-muted-foreground mt-1 text-sm">
							Each one will appear in Documents when it is ready.
						</p>
					</div>
				</div>

				{#if batchReport.rejected.length}
					<!-- Named, never counted: a bare number leaves the operator hunting for who was
					     missed. -->
					<Alert variant="warning">
						<AlertTitle>
							{batchReport.rejected.length} client{batchReport.rejected.length === 1 ? '' : 's'}
							skipped
						</AlertTitle>
						<AlertDescription>
							<ul class="mt-1 space-y-1">
								{#each batchReport.rejected as r (r.contactId)}
									<li>
										<span class="font-medium">{clientLabel(r.contactId)}</span> — {r.reason}
									</li>
								{/each}
							</ul>
						</AlertDescription>
					</Alert>
				{/if}

				<div class="flex flex-col gap-2">
					<Button href="/documents">View documents</Button>
					<Button variant="ghost" onclick={() => onClose?.()}>Close</Button>
				</div>
			</div>
		{:else}
			<!-- Form / review on the left, a live summary + the primary actions on the right. -->
			<div class="mt-6 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
				<div class="min-w-0">
					{#if genState === 'idle'}
						{#if canBatch}
							<!-- `Segmented` because this is a mode toggle and that is THE control for one
							     (docs/design-language.md) — it changes who the same template is generated
							     for, not what is generated. -->
							<div class="mb-4">
								<Segmented
									label="Generate for"
									options={[
										{ value: 'one', label: 'One client' },
										{ value: 'many', label: 'Several clients' }
									]}
									value={audience}
									onValueChange={(v) => setAudience(v as 'one' | 'many')}
								/>
							</div>
						{/if}
						{#if fields.length === 0}
							<Alert variant="warning" class="mb-4">
								<AlertDescription>
									This template has no input form linked, so only the core details are collected.
									Link a form on the template to capture more.
								</AlertDescription>
							</Alert>
						{/if}
						<DynamicForm
							bind:this={formRef}
							fields={allFields}
							bind:values
							{dataSources}
							showSubmit={false}
						/>
					{:else}
						<div class="space-y-4">
							{#each reviewSections as [section, sectionFields] (section)}
								{#if sectionFields.some((f) => reviewValue(f))}
									<Card>
										<CardContent>
											<h3 class="text-primary mb-3 text-sm font-semibold">{section}</h3>
											<dl class="-my-1 divide-y">
												{#each sectionFields as f (f.uid)}
													{@const val = reviewValue(f)}
													{#if val}
														<div class="flex items-start justify-between gap-4 py-2">
															<dt class="text-muted-foreground text-sm">{f.label}</dt>
															<dd class="text-foreground text-right text-sm font-medium">{val}</dd>
														</div>
													{/if}
												{/each}
											</dl>
										</CardContent>
									</Card>
								{/if}
							{/each}
						</div>
					{/if}
				</div>

				<aside class="space-y-4 lg:sticky lg:top-4">
					<Card>
						<CardContent>
							<p class="text-sm font-semibold">Summary</p>
							<dl class="mt-3 space-y-2.5 text-sm">
								<div class="flex items-center justify-between gap-3">
									<dt class="text-muted-foreground">Document</dt>
									<dd class="min-w-0 truncate text-right font-medium">{docName || '—'}</dd>
								</div>
								<div class="flex items-center justify-between gap-3">
									<dt class="text-muted-foreground flex items-center gap-1">
										{#if batchMode}
											<UsersIcon class="size-3.5" /> Clients
										{:else}
											<UserIcon class="size-3.5" /> Client
										{/if}
									</dt>
									<dd class="min-w-0 truncate text-right font-medium">{audienceSummary}</dd>
								</div>
							</dl>
							<p class="text-muted-foreground mt-3 border-t pt-3 text-xs">
								{#if genState === 'idle'}
									Fill in the details, then review. Fields marked
									<span class="text-destructive">*</span> are required.
								{:else if batchMode}
									Check the details, then generate. Each client gets their own document, with their
									own details merged in.
								{:else}
									Check the details, then generate. You'll land on the finished document.
								{/if}
							</p>
						</CardContent>
					</Card>

					{#if missingVars.length}
						<Alert variant="warning">
							<AlertTitle>Some template variables won't be filled</AlertTitle>
							<AlertDescription>
								Referenced by the template but not collected here — they'll render blank. Link a
								form that supplies them, or edit the template.
								<div class="mt-2 flex flex-wrap gap-1">
									{#each missingVars as v (v)}
										<code class="bg-muted rounded px-1.5 py-0.5 font-mono text-[11px]">{v}</code>
									{/each}
								</div>
							</AlertDescription>
						</Alert>
					{/if}

					{#if genError}
						<Alert variant="destructive">
							<AlertDescription>{genError}</AlertDescription>
						</Alert>
					{/if}

					<div class="flex flex-col gap-2">
						{#if genState === 'idle'}
							<Button onclick={toReview}>
								Review <ChevronRightIcon class="ml-1 size-4" />
							</Button>
							<Button variant="ghost" onclick={() => onClose?.()}>Cancel</Button>
						{:else}
							<!-- Disabled only in BATCH mode: the single-client field is `required`, so the form
							     validator already stops an empty one reaching review, and a disabled primary
							     action there would be a state nothing can explain. -->
							<Button onclick={generate} disabled={batchMode && chosenIds.length === 0}>
								<FileOutputIcon class="mr-1.5 size-4" />
								{#if batchMode}
									Generate {chosenIds.length} document{chosenIds.length === 1 ? '' : 's'}
								{:else}
									Generate document
								{/if}
							</Button>
							<Button variant="outline" onclick={() => (genState = 'idle')}>
								<ArrowLeftIcon class="mr-1.5 size-4" /> Back to details
							</Button>
						{/if}
					</div>
				</aside>
			</div>
		{/if}
	{/if}
</div>
