<script lang="ts">
	import {
		Button,
		Checkbox,
		Combobox,
		Input,
		Label,
		Segmented,
		SelectMenu,
		Textarea
	} from '@graphene/ui';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import {
		type FlowNode,
		STEP_TYPE_LABELS,
		STEP_TYPE_HINTS,
		STEP_TYPE_DEFAULT_ASSIGNEE,
		STEP_TYPE_DEFAULT_SCOPE,
		SCOPE_LABELS,
		type FlowStep,
		type FlowStepType,
		type FlowScope,
		type FlowForm,
		type FlowDocument,
		type FlowTemplate,
		type FlowTaskSpec,
		MAPPING_TRANSFORMS,
		MAPPING_TARGETS,
		mappingLabel,
		readMapping,
		type FieldMapping
	} from '@graphene/ui/book/journeys';
	import { providerItems } from './inspectors/shared';

	let {
		node,
		nodes = [],
		forms,
		documents,
		templates,
		terms = [],
		roles = [],
		canManage,
		formFieldsEndpoint = '/flows/form-fields',
		onchange,
		onrestructure
	}: {
		/** The selected node. Its kind is `step`, which structurally IS a legacy `FlowStep` plus
		 *  `kind`/`next` — the whole reason the v3 upgrade left the authored step shape alone — so
		 *  everything below reads and writes it as one. */
		node: FlowNode;
		/** Every node in the draft — the candidate prerequisites for the `depends_on` picker. */
		nodes?: FlowNode[];
		forms: FlowForm[];
		documents: FlowDocument[];
		templates: FlowTemplate[];
		terms?: { uid: string; title: string }[];
		/** Firm roles for the raise-a-task picker; empty ⇒ free-text slug input. */
		roles?: { slug: string; name: string }[];
		canManage: boolean;
		formFieldsEndpoint?: string;
		onchange: () => void;
		/** Called on edits that change the graph's shape (tier / condition) so it re-lays-out. */
		onrestructure: () => void;
	} = $props();

	// The same object, read as what it is. Mutations below write straight through to the node the
	// canvas holds — editing in place is the inspector contract (inspectors/types.ts).
	const step = $derived(node as FlowStep);
	const steps = $derived(nodes.filter((n) => n.kind === 'step') as unknown as FlowStep[]);

	const STEP_TYPES = Object.keys(STEP_TYPE_LABELS) as FlowStepType[];
	const SCOPES: FlowScope[] = ['participant', 'account', 'application'];

	// SelectMenu item lists (static ones once; prop-driven ones derived).
	const typeItems = STEP_TYPES.map((t) => ({ value: t, label: STEP_TYPE_LABELS[t] }));
	const scopeItems = SCOPES.map((s) => ({ value: s, label: SCOPE_LABELS[s] }));
	const assigneeItems = [
		{ value: 'client', label: 'Client' },
		{ value: 'staff', label: 'Staff' }
	];
	const gateItems = [
		{ value: 'none', label: 'None' },
		{ value: 'offshore_approved', label: 'Offshore approved' }
	];
	const taskOnItems = [
		{ value: 'completed', label: 'It completes' },
		{ value: 'failed', label: 'It fails' },
		{ value: 'blocked', label: 'It blocks' },
		{ value: 'waiting', label: 'It waits' }
	];
	const taskAssignKindItems = [
		{ value: 'role', label: 'A team' },
		{ value: 'user', label: 'A person' },
		{ value: 'participant', label: 'The client' }
	];
	const taskCategoryItems = [
		{ value: 'generic', label: 'Task' },
		{ value: 'review', label: 'Review' },
		{ value: 'acknowledge', label: 'Acknowledge' },
		{ value: 'upload', label: 'Upload' },
		{ value: 'follow_up', label: 'Follow up' }
	];
	const taskPriorityItems = [
		{ value: 'low', label: 'Low' },
		{ value: 'normal', label: 'Normal' },
		{ value: 'high', label: 'High' }
	];
	const formItems = $derived(forms.map((f) => ({ value: f.uid, label: f.name })));

	/** What the selected form ASKS. Fetched lazily per uid (`flows/form-fields`) rather than loaded
	 *  with the page: the author has not chosen a form yet on most opens, and fetching every form's
	 *  fields up front to serve one choice is the wrong trade. Cached per uid so re-selecting a form
	 *  is free, and re-read whenever the selection changes so a field added in the forms section
	 *  shows up on the next selection rather than the next reload. */
	type FormField = {
		uid: string;
		field_key: string;
		field_type: string;
		label: string;
		section: string | null;
		required: boolean;
	};
	let fieldCache = $state<Record<string, FormField[]>>({});
	let fieldsFor = $state<string | null>(null);
	let fieldsLoading = $state(false);
	let fieldsFailed = $state(false);

	async function loadFields(uid: string) {
		if (!uid || fieldCache[uid]) return;
		fieldsLoading = true;
		fieldsFailed = false;
		try {
			const res = await fetch(`${formFieldsEndpoint}?uid=${encodeURIComponent(uid)}`);
			if (!res.ok) throw new Error(String(res.status));
			const body = await res.json();
			fieldCache = { ...fieldCache, [uid]: (body.fields ?? []) as FormField[] };
		} catch {
			fieldsFailed = true;
		} finally {
			fieldsLoading = false;
		}
	}

	$effect(() => {
		const uid = step.type === 'fill_form' ? cfg('form_uid') : '';
		if (uid && uid !== fieldsFor) {
			fieldsFor = uid;
			void loadFields(uid);
		}
	});

	const fields = $derived(fieldCache[cfg('form_uid')] ?? []);
	/** Grouped the way the form itself is, so the inspector reads like the thing the client sees. */
	const fieldSections = $derived(
		fields.reduce<{ name: string; fields: FormField[] }[]>((acc, f) => {
			const name = f.section || 'Questions';
			const last = acc[acc.length - 1];
			if (last && last.name === name) last.fields.push(f);
			else acc.push({ name, fields: [f] });
			return acc;
		}, [])
	);
	const documentItems = $derived(documents.map((d) => ({ value: d.uid, label: d.name })));
	const templateItems = $derived(templates.map((t) => ({ value: t.uid, label: t.name })));
	const roleItems = $derived(roles.map((r) => ({ value: r.slug, label: r.name })));

	function cfg(key: string): string {
		const v = step.config[key];
		return v == null ? '' : String(v);
	}
	const TERMS_SOURCE_OPTIONS = [
		{ value: 'library', label: 'Library term' },
		{ value: 'text', label: 'Written declaration' },
		{ value: 'document', label: 'PDF document' }
	] as const;
	// The toggle is local so switching source before picking one does not snap back to
	// the empty written-declaration state. Reset when the selected step changes.
	let termsSourceOverride = $state<'library' | 'text' | 'document' | null>(null);
	$effect(() => {
		void step.id;
		termsSourceOverride = null;
	});
	const termsSource = $derived(
		termsSourceOverride ?? (cfg('term_uid') ? 'library' : cfg('document_uid') ? 'document' : 'text')
	);
	const termItems = $derived(terms.map((t) => ({ value: t.uid, label: t.title })));
	/** The write-back rows. `mapping` was a legal config key on all three sides — the contract, the
	 *  validator and the runtime — and authorable NOWHERE: the only mappings that existed were the
	 *  ones seeded in `onboarding_seed.py`, so a firm authoring its own fact-find had no way to make
	 *  a single answer reach the client record. */
	const mapping = $derived(readMapping(step.config.mapping));

	function writeMapping(rows: FieldMapping[]) {
		// An empty list is an ABSENT key, not `[]`: the runtime treats both the same, but a config
		// carrying dead keys is one an author has to read past.
		if (rows.length)
			step.config.mapping = rows.map((r) => ({ ...r, transforms: r.transforms ?? [] }));
		else delete step.config.mapping;
		onchange();
	}
	const editRow = (i: number, patch: Partial<FieldMapping>) =>
		writeMapping(mapping.map((r, k) => (k === i ? { ...r, ...patch } : r)));
	const dropRow = (i: number) => writeMapping(mapping.filter((_, k) => k !== i));
	const addRow = () => writeMapping([...mapping, { source: '', target: '', transforms: [] }]);

	/** Sources are the form's own field keys, so a mapping cannot name a question that is not asked.
	 *  Free text stays possible for a key the fetch has not returned (a form still loading, or one
	 *  the caller lacks `forms:read` for) — refusing to render a stored row would look like data
	 *  loss. */
	const sourceItems = $derived(
		fields.map((f) => ({ value: f.field_key, label: `${f.label} · ${f.field_key}` }))
	);
	const targetItems = MAPPING_TARGETS.map((t) => ({ value: t, label: mappingLabel(t) }));
	/** A target that is not a column is not an error — it nests into `data`. Saying so inline is
	 *  what stops an author reading a silent jsonb write as a failed one. */
	const isDataPath = (target: string) => !!target && !MAPPING_TARGETS.includes(target);

	function setCfg(key: string, value: string) {
		if (value === '') delete step.config[key];
		else step.config[key] = value;
		onchange();
	}
	function changeType(type: FlowStepType) {
		step.type = type;
		step.assignee = STEP_TYPE_DEFAULT_ASSIGNEE[type];
		step.scope = STEP_TYPE_DEFAULT_SCOPE[type];
		step.config = {};
		onrestructure();
	}

	// ── Raise-a-task side-effects: edit step.tasks in place, mark dirty + refresh the badge. ──
	function addTaskSpec() {
		step.tasks = [
			...(step.tasks ?? []),
			{ on: 'completed', assign_to: { kind: 'role', role: roles[0]?.slug ?? '' }, title: '' }
		];
		onrestructure(); // the node badge shows the count, so the canvas needs a rebuild
	}
	function removeTaskSpec(i: number) {
		step.tasks = (step.tasks ?? []).filter((_, ix) => ix !== i);
		onrestructure();
	}
	function setTaskSpec(i: number, patch: Partial<FlowTaskSpec>) {
		const specs = step.tasks ?? [];
		specs[i] = { ...specs[i], ...patch } as FlowTaskSpec;
		step.tasks = specs;
		onchange();
	}
	function setTaskAssignKind(i: number, kind: string) {
		if (kind === 'role')
			setTaskSpec(i, { assign_to: { kind: 'role', role: roles[0]?.slug ?? '' } });
		else if (kind === 'user') setTaskSpec(i, { assign_to: { kind: 'user', user_id: '' } });
		else setTaskSpec(i, { assign_to: { kind: 'participant' } });
	}

	// ── Ordering (`depends_on`): real, enforced prerequisites. Until every one settles the engine
	//    creates no artifact for this step — no form request, no identity check — so "AML only after
	//    identity" is a rule, not a label. Only steps at the SAME tier can gate one another (the
	//    engine resolves a prerequisite within the case), and a step can't depend on itself.
	const dependencyOptions = $derived(
		steps.filter(
			(s) => s.id !== step.id && (s.scope ?? 'application') === (step.scope ?? 'application')
		)
	);
	const dependsOn = $derived(step.depends_on ?? []);
	function toggleDependency(id: string, on: boolean) {
		const next = on ? [...dependsOn, id] : dependsOn.filter((d) => d !== id);
		step.depends_on = next;
		onrestructure(); // the canvas draws these as real edges
	}
	/** A cycle would wedge every step in it forever, so the server rejects it at save — warn here
	 *  rather than letting the author discover it as a 422. */
	function wouldCycle(candidate: string): boolean {
		const byId = new Map(steps.map((s) => [s.id, s.depends_on ?? []]));
		const seen = new Set<string>();
		const walk = (id: string): boolean => {
			if (id === step.id) return true;
			if (seen.has(id)) return false;
			seen.add(id);
			return (byId.get(id) ?? []).some(walk);
		};
		return walk(candidate);
	}

	// A step no longer carries a condition HERE. `visibility` remains in the document — the engine
	// evaluates it at plan time, legacy definitions in production are full of it, and the seeded
	// onboarding still uses it for the one question that is genuinely per-participant (see
	// `onboarding_seed._build_definition`) — but the thing an author reaches for is a `condition`
	// NODE on the canvas: a fork with two arms, which can be seen, named, moved and re-wired.
	// Converting a legacy flow turns each step's condition into exactly that
	// (`convert.ts::convertLegacy`).
	//
	// Do not restore this block. Two places to decide whether one step happens is one too many, and
	// the half that lived here was invisible on the graph.
</script>

<!-- The panel chrome (title, delete, close) belongs to InspectorPanel, which is the one place it
     exists for every kind of node. This is the step's fields and nothing else. -->
<div class="space-y-4">
	<div class="space-y-1.5">
		<Label>Type</Label>
		<SelectMenu
			size="sm"
			items={typeItems}
			value={step.type}
			disabled={!canManage}
			onValueChange={(v) => changeType(v as FlowStepType)}
		/>
		<p class="text-muted-foreground text-xs">{STEP_TYPE_HINTS[step.type]}</p>
	</div>

	<div class="space-y-1.5">
		<Label>Title</Label>
		<Input
			value={step.title}
			disabled={!canManage}
			placeholder="e.g. Your details"
			oninput={(e) => {
				step.title = e.currentTarget.value;
				onchange();
			}}
		/>
	</div>

	<div class="grid grid-cols-2 gap-3">
		<div class="space-y-1.5">
			<Label>Assignee</Label>
			<SelectMenu
				size="sm"
				items={assigneeItems}
				value={step.assignee}
				disabled={!canManage}
				onValueChange={(v) => {
					step.assignee = v as 'client' | 'staff';
					onchange();
				}}
			/>
		</div>
		<div class="space-y-1.5">
			<Label>Runs at</Label>
			<SelectMenu
				size="sm"
				items={scopeItems}
				value={step.scope}
				disabled={!canManage}
				onValueChange={(v) => {
					step.scope = v as FlowScope;
					onrestructure();
				}}
			/>
		</div>
	</div>

	<div class="flex items-center gap-4">
		<div class="flex items-center gap-2">
			<Checkbox
				id="step-required"
				checked={step.required}
				disabled={!canManage}
				onCheckedChange={(v) => {
					step.required = v === true;
					onchange();
				}}
			/>
			<Label for="step-required" class="font-normal">Required</Label>
		</div>
		{#if step.scope === 'account'}
			<div class="flex items-center gap-2">
				<Checkbox
					id="step-per-owner"
					checked={step.per_participant}
					disabled={!canManage}
					onCheckedChange={(v) => {
						step.per_participant = v === true;
						onchange();
					}}
				/>
				<Label for="step-per-owner" class="font-normal">One per owner</Label>
			</div>
		{/if}
	</div>

	<!-- Type-specific config -->
	{#if step.type === 'fill_form'}
		<div class="space-y-1.5">
			<Label>Form</Label>
			<SelectMenu
				size="sm"
				items={formItems}
				value={cfg('form_uid')}
				placeholder="Choose a form…"
				disabled={!canManage}
				onValueChange={(v) => setCfg('form_uid', v)}
			/>
			<!-- What this step actually asks. The whole reason the panel exists: a dropdown of form
			     NAMES told an author nothing about the questions, and checking meant leaving the
			     editor for /forms. -->
			{#if cfg('form_uid')}
				{#if fieldsLoading}
					<p class="text-muted-foreground text-xs">Reading the questions…</p>
				{:else if fieldsFailed}
					<p class="text-destructive text-xs">Couldn’t load this form’s questions.</p>
				{:else if !fields.length}
					<p class="text-muted-foreground text-xs">
						This form has no questions yet — the step would ask the client nothing.
					</p>
				{:else}
					<details class="border-border mt-1 rounded-lg border">
						<summary
							class="text-muted-foreground hover:text-foreground cursor-pointer px-2.5 py-1.5 text-xs"
						>
							Asks {fields.length}
							{fields.length === 1 ? 'question' : 'questions'}
						</summary>
						<div class="space-y-2 px-2.5 pt-0.5 pb-2">
							{#each fieldSections as section (section.name)}
								<div>
									<p class="text-muted-foreground/70 text-[11px] font-semibold">
										{section.name}
									</p>
									<ul class="mt-0.5 space-y-0.5">
										{#each section.fields as f (f.uid)}
											<li class="flex items-baseline gap-1.5 text-xs">
												<span class="min-w-0 flex-1 truncate">{f.label}</span>
												{#if f.required}
													<span class="text-warning shrink-0 text-[10px]">required</span>
												{/if}
												<!-- The KEY, not just the label: it is what a mapping's `source` names,
												     and what an author has to get exactly right. -->
												<code class="text-muted-foreground/70 shrink-0 text-[10px]"
													>{f.field_key}</code
												>
											</li>
										{/each}
									</ul>
								</div>
							{/each}
						</div>
					</details>
				{/if}
			{/if}
		</div>

		<!-- Where the answers GO. Without this the fact-find was a form that filed a response and
		     nothing more: the client record stayed empty unless someone had seeded a mapping in
		     Python. `source` is the question, `target` the field on the client. -->
		<div class="space-y-1.5">
			<div class="flex items-baseline justify-between gap-2">
				<Label>Save answers onto the client</Label>
				{#if canManage}
					<Button variant="ghost" size="xs" onclick={addRow} disabled={!cfg('form_uid')}>
						+ Add
					</Button>
				{/if}
			</div>
			{#if !mapping.length}
				<p class="text-muted-foreground text-xs">
					{cfg('form_uid')
						? 'Answers stay with the form and are not added to the client record.'
						: 'Choose a form first.'}
				</p>
			{/if}
			{#each mapping as row, i (i)}
				<div class="border-border space-y-1.5 rounded-lg border p-2">
					<div class="flex items-center gap-1.5">
						<Combobox
							class="min-w-0 flex-1"
							options={sourceItems}
							bind:value={() => row.source, (v) => editRow(i, { source: v })}
							allowCustom
							placeholder="Question…"
							emptyText="No matching question."
							aria-label="Question"
							disabled={!canManage}
						/>
						{#if canManage}
							<Button
								variant="ghost"
								size="icon"
								class="text-muted-foreground hover:text-destructive size-7 shrink-0"
								aria-label="Remove"
								onclick={() => dropRow(i)}
							>
								<Trash2Icon class="size-3.5" />
							</Button>
						{/if}
					</div>
					<Combobox
						options={targetItems}
						bind:value={() => row.target, (v) => editRow(i, { target: v })}
						allowCustom
						placeholder="Field on the client…"
						emptyText="No matching field. It will be saved under the client’s extra details."
						aria-label="Field on the client"
						disabled={!canManage}
					/>
					{#if isDataPath(row.target)}
						<p class="text-muted-foreground text-[11px]">
							No matching field. Saved under the client’s extra details as
							<code>{row.target}</code>.
						</p>
					{/if}
					<div class="flex flex-wrap gap-x-3 gap-y-1">
						{#each MAPPING_TRANSFORMS as t (t)}
							<label class="flex items-center gap-1 text-[11px]">
								<input
									type="checkbox"
									class="size-3"
									disabled={!canManage}
									checked={(row.transforms ?? []).includes(t)}
									onchange={(e) => {
										const on = (e.currentTarget as HTMLInputElement).checked;
										const have = row.transforms ?? [];
										// Order is the pipeline, so a re-ticked transform goes on the END rather
										// than back where it was — `trim` then `upper` is not `upper` then `trim`
										// for every input, and the author's last action is the one to honour.
										editRow(i, { transforms: on ? [...have, t] : have.filter((x) => x !== t) });
									}}
								/>
								{t}
							</label>
						{/each}
					</div>
				</div>
			{/each}
		</div>
	{:else if step.type === 'accept_terms'}
		<div class="space-y-3">
			<div class="space-y-1.5">
				<Label>The client agrees to</Label>
				<Segmented
					size="sm"
					shape="square"
					label="Declaration source"
					options={TERMS_SOURCE_OPTIONS}
					value={termsSource}
					disabled={!canManage}
					onValueChange={(v) => {
						termsSourceOverride =
							v === 'document' ? 'document' : v === 'library' ? 'library' : 'text';
						if (v === 'document') {
							setCfg('content', '');
							setCfg('term_uid', '');
						} else if (v === 'library') {
							setCfg('content', '');
							setCfg('document_uid', '');
						} else {
							setCfg('document_uid', '');
							setCfg('term_uid', '');
						}
					}}
				/>
			</div>
			{#if termsSource === 'library'}
				<div class="space-y-1.5">
					<Label>Library term</Label>
					<SelectMenu
						size="sm"
						items={termItems}
						value={cfg('term_uid')}
						placeholder="Choose published terms…"
						disabled={!canManage}
						onValueChange={(v) => setCfg('term_uid', v)}
					/>
				</div>
			{:else if termsSource === 'document'}
				<div class="space-y-1.5">
					<Label>Document</Label>
					<SelectMenu
						size="sm"
						items={documentItems}
						value={cfg('document_uid')}
						placeholder="Choose a PDF…"
						disabled={!canManage}
						onValueChange={(v) => setCfg('document_uid', v)}
					/>
				</div>
			{:else}
				<div class="space-y-1.5">
					<Label>Declaration</Label>
					<Textarea
						rows={10}
						value={cfg('content')}
						disabled={!canManage}
						placeholder="The text the client will read and tick. Markdown is supported."
						oninput={(e) => setCfg('content', e.currentTarget.value)}
					/>
					<p class="text-muted-foreground text-xs">
						Headings, lists, bold and links. Or paste HTML.
					</p>
				</div>
			{/if}
		</div>
	{:else if step.type === 'generate_document'}
		<div class="space-y-1.5">
			<Label>Template</Label>
			<SelectMenu
				size="sm"
				items={templateItems}
				value={cfg('template_uid')}
				placeholder="Choose a template…"
				disabled={!canManage}
				onValueChange={(v) => setCfg('template_uid', v)}
			/>
		</div>
	{:else if step.type === 'verify_identity'}
		<div class="space-y-1.5">
			<Label>Provider</Label>
			<SelectMenu
				size="sm"
				items={providerItems(cfg('provider'))}
				value={cfg('provider') || 'manual'}
				disabled={!canManage}
				onValueChange={(v) => setCfg('provider', v)}
			/>
		</div>
	{:else if step.type === 'review'}
		<div class="space-y-1.5">
			<Label>Raises gate</Label>
			<SelectMenu
				size="sm"
				items={gateItems}
				value={cfg('gate') || 'none'}
				disabled={!canManage}
				onValueChange={(v) => setCfg('gate', v === 'none' ? '' : v)}
			/>
		</div>
	{:else if step.type === 'task'}
		<div class="space-y-1.5">
			<Label>Description</Label>
			<Input
				value={cfg('description')}
				disabled={!canManage}
				placeholder="What to do…"
				oninput={(e) => setCfg('description', e.currentTarget.value)}
			/>
		</div>
	{/if}

	<!-- Ordering: enforced prerequisites. Nothing is created for this step until they all settle. -->
	<div class="space-y-2 border-t pt-4">
		<Label class="text-xs">Runs after</Label>
		{#if !dependencyOptions.length}
			<p class="text-muted-foreground/70 text-xs">
				Add another step at the same tier to order this one behind it.
			</p>
		{:else}
			<p class="text-muted-foreground/70 text-xs">
				This step isn't created — no form, no check, nothing for anyone to do — until every step
				ticked here is finished.
			</p>
			{#each dependencyOptions as dep (dep.id)}
				{@const checked = dependsOn.includes(dep.id)}
				{@const cyclic = !checked && wouldCycle(dep.id)}
				<div class="flex items-start gap-2">
					<Checkbox
						id={`dep-${dep.id}`}
						{checked}
						disabled={!canManage || cyclic}
						onCheckedChange={(v) => toggleDependency(dep.id, v === true)}
					/>
					<Label for={`dep-${dep.id}`} class="font-normal">
						{dep.title || dep.id}
						{#if cyclic}
							<span class="text-muted-foreground block text-xs">
								Can't: that step already waits on this one.
							</span>
						{/if}
					</Label>
				</div>
			{/each}
		{/if}
	</div>

	<!-- Raise-a-task side-effects (docs/tasks.md): fire-and-forget follow-ups on a trigger. -->
	<div class="space-y-2 border-t pt-4">
		<div class="flex items-center justify-between">
			<Label class="text-xs">Raise a task</Label>
			{#if canManage}
				<Button variant="link" size="xs" class="text-brand px-0" onclick={addTaskSpec}>
					+ Add
				</Button>
			{/if}
		</div>
		{#if !(step.tasks ?? []).length}
			<p class="text-muted-foreground/70 text-xs">
				e.g. when this step fails, raise a review task for Compliance.
			</p>
		{/if}
		{#each step.tasks ?? [] as t, i (i)}
			<div class="space-y-2 rounded-lg border p-2.5">
				<div class="flex items-center gap-1.5">
					<span class="text-muted-foreground shrink-0 text-xs">When</span>
					<SelectMenu
						size="sm"
						class="flex-1"
						items={taskOnItems}
						value={t.on}
						disabled={!canManage}
						onValueChange={(v) => setTaskSpec(i, { on: v as FlowTaskSpec['on'] })}
					/>
					{#if canManage}
						<Button
							variant="ghost"
							size="icon-xs"
							class="text-muted-foreground hover:text-destructive shrink-0"
							aria-label="Remove task"
							onclick={() => removeTaskSpec(i)}
						>
							<Trash2Icon class="size-3.5" />
						</Button>
					{/if}
				</div>
				<div class="flex items-center gap-1.5">
					<span class="text-muted-foreground shrink-0 text-xs">assign</span>
					<SelectMenu
						size="sm"
						class="w-24 shrink-0"
						items={taskAssignKindItems}
						value={t.assign_to.kind}
						disabled={!canManage}
						onValueChange={(v) => setTaskAssignKind(i, v)}
					/>
					{#if t.assign_to.kind === 'role'}
						{#if roles.length}
							<SelectMenu
								size="sm"
								class="flex-1"
								items={roleItems}
								value={t.assign_to.role}
								placeholder="Choose a role…"
								disabled={!canManage}
								onValueChange={(v) => setTaskSpec(i, { assign_to: { kind: 'role', role: v } })}
							/>
						{:else}
							<Input
								class="flex-1 text-xs"
								value={t.assign_to.role}
								disabled={!canManage}
								placeholder="role slug, e.g. compliance"
								oninput={(e) =>
									setTaskSpec(i, { assign_to: { kind: 'role', role: e.currentTarget.value } })}
							/>
						{/if}
					{:else if t.assign_to.kind === 'user'}
						<Input
							class="flex-1 text-xs"
							value={t.assign_to.user_id}
							disabled={!canManage}
							placeholder="staff user id"
							oninput={(e) =>
								setTaskSpec(i, {
									assign_to: { kind: 'user', user_id: e.currentTarget.value }
								})}
						/>
					{:else}
						<span class="text-muted-foreground flex-1 text-xs">this step's participant</span>
					{/if}
				</div>
				<Input
					class="text-xs"
					placeholder={'Task title. You can use {participant_name}, {flow_name} and {step_title}'}
					value={t.title}
					disabled={!canManage}
					oninput={(e) => setTaskSpec(i, { title: e.currentTarget.value })}
				/>
				<div class="flex items-center gap-1.5">
					<SelectMenu
						size="sm"
						class="flex-1"
						items={taskCategoryItems}
						value={t.category ?? 'generic'}
						disabled={!canManage}
						onValueChange={(v) => setTaskSpec(i, { category: v as FlowTaskSpec['category'] })}
					/>
					<SelectMenu
						size="sm"
						class="flex-1"
						items={taskPriorityItems}
						value={t.priority ?? 'normal'}
						disabled={!canManage}
						onValueChange={(v) => setTaskSpec(i, { priority: v as FlowTaskSpec['priority'] })}
					/>
					<Input
						class="w-20 text-xs"
						type="number"
						min="0"
						value={t.due_days ?? ''}
						disabled={!canManage}
						placeholder="due (d)"
						title="Due N days after it's raised"
						oninput={(e) =>
							setTaskSpec(i, {
								due_days: e.currentTarget.value === '' ? null : Number(e.currentTarget.value)
							})}
					/>
				</div>
			</div>
		{/each}
	</div>
</div>
