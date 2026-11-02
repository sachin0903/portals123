<script lang="ts">
	import type {
		FormField,
		FieldConditions,
		FieldValidation,
		FieldOptions,
		RepeatingColumn
	} from '@graphene/ui';
	import {
		Sheet,
		Button,
		Input,
		Label,
		SelectMenu,
		StatusBadge,
		Textarea,
		Switch,
		FORM_FIELD_TYPES,
		OPTION_REQUIRED_TYPES,
		REPEATING_COLUMN_TYPES,
		ALL_STANDARD_FIELDS,
		slugifyCode
	} from '@graphene/ui';
	import { untrack } from 'svelte';
	import AlertCircleIcon from '@lucide/svelte/icons/circle-alert';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import ShieldCheckIcon from '@lucide/svelte/icons/shield-check';
	import ConditionsEditor from './conditions-editor.svelte';
	import ValidationEditor from './validation-editor.svelte';

	let {
		open = $bindable(false),
		field = null,
		siblingFields = [],
		onsave
	}: {
		open?: boolean;
		field?: FormField | null;
		/** the form's other fields (for the conditions "field" picker). */
		siblingFields?: FormField[];
		onsave?: (field: FormField) => void;
	} = $props();

	type OptionRow = { value: string; label: string };

	let key = $state('');
	let label = $state('');
	let fieldType = $state('text');
	let description = $state('');
	let required = $state(false);
	let defaultValue = $state('');
	let conditions = $state<FieldConditions>(null);
	let validation = $state<FieldValidation | null>(null);
	let error = $state('');
	// Whether the field key has been set explicitly (typed, or an existing field's stored key). While
	// false, the key auto-derives from the label — like the Reference code fields elsewhere — so a
	// new field never submits with an empty key.
	let keyEdited = $state(false);

	// Options: a friendly row editor for choices, a plain code for currency, and a
	// JSON escape hatch for advanced shapes ({ source }).
	let optionRows = $state<OptionRow[]>([]);
	let optionsJson = $state('');
	let optionsAdvanced = $state(false);
	let currencyCode = $state('');

	type ColumnDraft = {
		key: string;
		label: string;
		type: string;
		required: boolean;
		choices: OptionRow[];
		keyEdited: boolean;
	};
	let columns = $state<ColumnDraft[]>([]);

	function blankColumn(): ColumnDraft {
		return { key: '', label: '', type: 'text', required: true, choices: [], keyEdited: false };
	}

	function columnsFrom(opts: FieldOptions | RepeatingColumn[] | null): ColumnDraft[] {
		if (!Array.isArray(opts)) return [];
		const drafts: ColumnDraft[] = [];
		for (const item of opts) {
			if (!item || typeof item !== 'object' || !('key' in item)) continue;
			const record = item as unknown as Record<string, unknown>;
			const type = typeof record.type === 'string' && record.type ? record.type : 'text';
			drafts.push({
				key: String(record.key ?? ''),
				label: String(record.label ?? ''),
				type,
				required: record.required !== false,
				choices: Array.isArray(record.options) ? toRows(record.options as FieldOptions) : [],
				keyEdited: Boolean(record.key)
			});
		}
		return drafts;
	}

	// Progressive disclosure — advanced sections start collapsed unless in use.
	let showConditions = $state(false);
	let showValidation = $state(false);

	function deepClone<T>(v: T): T {
		return v == null ? v : (JSON.parse(JSON.stringify(v)) as T);
	}

	function toRows(opts: FieldOptions): OptionRow[] {
		if (!Array.isArray(opts)) return [];
		return opts.map((o) => {
			if (typeof o === 'string') return { value: o, label: o };
			const rec = o as Record<string, unknown>;
			const value = rec.value ?? rec.code ?? '';
			const lbl = rec.label ?? rec.name ?? rec.adjective ?? '';
			return { value: String(value), label: String(lbl) };
		});
	}

	// Re-seed the working copy each time the sheet opens (so cancelling and
	// reopening starts from the stored field, not the last uncommitted edit).
	$effect(() => {
		const f = field;
		if (!open) return;
		untrack(() => {
			key = f?.fieldKey ?? '';
			label = f?.label ?? '';
			// Existing key ⇒ treat as manually set (don't rewrite it); blank ⇒ auto-derive from label.
			keyEdited = Boolean(f?.fieldKey);
			fieldType = f?.fieldType ?? 'text';
			description = f?.description ?? '';
			required = f?.required ?? false;
			defaultValue = f?.validation?.defaultValue != null ? String(f.validation.defaultValue) : '';
			conditions = deepClone(f?.conditions ?? null);
			// Width (a 1–12 column-span layout hint) lives in the validation bag and is set by
			// dragging in the builder, not here — keep it untouched on the working copy.
			validation = deepClone(f?.validation ?? null);
			error = '';

			// Options.
			const opts = (f?.options ?? null) as FieldOptions;
			const isCurrencyType = fieldType === 'currency' || fieldType === 'money';
			currencyCode = '';
			optionRows = [];
			optionsJson = '';
			optionsAdvanced = false;
			if (isCurrencyType) {
				if (opts && typeof opts === 'object' && !Array.isArray(opts) && 'currency' in opts) {
					currencyCode = String((opts as { currency?: unknown }).currency ?? '');
				}
			} else if (Array.isArray(opts)) {
				optionRows = toRows(opts);
			} else if (opts && typeof opts === 'object') {
				// { source } / { options: [] } → advanced JSON.
				optionsAdvanced = true;
				optionsJson = JSON.stringify(opts, null, 2);
			}
			if (
				OPTION_REQUIRED_TYPES.includes(fieldType) &&
				!optionsAdvanced &&
				optionRows.length === 0
			) {
				optionRows = [{ value: '', label: '' }];
			}

			columns = fieldType === 'repeating' ? columnsFrom(opts) : [];
			if (fieldType === 'repeating' && columns.length === 0) columns = [blankColumn()];

			showConditions = Boolean(f?.conditions);
			showValidation = Boolean(f?.validation && Object.keys(f.validation).length > 0);
		});
	});

	const requiresOptions = $derived(OPTION_REQUIRED_TYPES.includes(fieldType));
	const isCurrency = $derived(fieldType === 'currency' || fieldType === 'money');
	const isDisplay = $derived(fieldType === 'display');
	const isRepeating = $derived(fieldType === 'repeating');
	const showChoices = $derived(requiresOptions);
	const availableKeys = $derived(
		siblingFields
			.filter((f) => f.uid !== field?.uid && f.fieldKey)
			.map((f) => ({ value: f.fieldKey, label: f.label || f.fieldKey }))
	);
	const isEdit = $derived(Boolean(field && field.label));

	// Prefill label/type from the standard-field library when the key matches; typing here means the
	// key is now user-owned, so stop auto-deriving it from the label.
	function onKeyInput() {
		keyEdited = true;
		const std = ALL_STANDARD_FIELDS.find((s) => s.key === key.trim());
		if (std && !label.trim()) label = std.label;
	}

	// Auto-derive the field key from the label (lower_snake_case) until the user edits the key.
	function onLabelInput() {
		if (!keyEdited) key = slugifyCode(label);
	}

	function addChoice() {
		optionRows = [...optionRows, { value: '', label: '' }];
	}
	function removeChoice(i: number) {
		optionRows = optionRows.filter((_, idx) => idx !== i);
	}
	function toggleAdvanced() {
		if (!optionsAdvanced) {
			// list → JSON: serialise current rows.
			const rows = optionRows
				.map((r) => ({
					value: r.value.trim() || r.label.trim(),
					label: r.label.trim() || r.value.trim()
				}))
				.filter((r) => r.value);
			optionsJson = JSON.stringify(rows, null, 2);
			optionsAdvanced = true;
		} else {
			// JSON → list: parse an array back into rows if we can.
			try {
				const parsed = JSON.parse(optionsJson || '[]');
				if (Array.isArray(parsed)) optionRows = toRows(parsed as FieldOptions);
			} catch {
				/* leave rows as-is */
			}
			optionsAdvanced = false;
		}
	}

	function clearConditions() {
		conditions = null;
		showConditions = false;
	}
	function clearValidation() {
		validation = null;
		showValidation = false;
	}

	function choiceRows(rows: OptionRow[]): { value: string; label: string }[] {
		return rows
			.map((row) => ({
				value: row.value.trim() || row.label.trim(),
				label: row.label.trim() || row.value.trim()
			}))
			.filter((row) => row.value);
	}

	function buildOptions(): { ok: boolean; options: FormField['options'] } {
		if (isRepeating) {
			const built: RepeatingColumn[] = [];
			const seen = new Set<string>();
			for (const column of columns) {
				const label = column.label.trim();
				const key = column.key.trim() || slugifyCode(label);
				if (!label || !key) {
					error = 'Each column needs a label.';
					return { ok: false, options: null };
				}
				if (seen.has(key)) {
					error = `Two columns use the key "${key}".`;
					return { ok: false, options: null };
				}
				seen.add(key);
				const entry: RepeatingColumn = { key, label, type: column.type || 'text' };
				if (!column.required) entry.required = false;
				if (OPTION_REQUIRED_TYPES.includes(column.type)) {
					const rows = choiceRows(column.choices);
					if (rows.length === 0) {
						error = `Add at least one choice for ${label}.`;
						return { ok: false, options: null };
					}
					entry.options = rows;
				}
				built.push(entry);
			}
			if (built.length === 0) {
				error = 'Add at least one column.';
				return { ok: false, options: null };
			}
			return { ok: true, options: built };
		}
		if (isCurrency) {
			return { ok: true, options: currencyCode.trim() ? { currency: currencyCode.trim() } : null };
		}
		if (!requiresOptions) return { ok: true, options: null };

		if (optionsAdvanced) {
			const raw = optionsJson.trim();
			if (!raw) {
				error = 'Add at least one choice.';
				return { ok: false, options: null };
			}
			let parsed: unknown;
			try {
				parsed = JSON.parse(raw);
			} catch {
				error = 'Advanced options must be valid JSON.';
				return { ok: false, options: null };
			}
			const okShape =
				(Array.isArray(parsed) && parsed.length > 0) ||
				(!!parsed &&
					typeof parsed === 'object' &&
					typeof (parsed as Record<string, unknown>).source === 'string');
			if (!okShape) {
				error = 'Advanced options must be a non-empty array or an object with a "source".';
				return { ok: false, options: null };
			}
			return { ok: true, options: parsed as FieldOptions };
		}

		const rows = choiceRows(optionRows);
		if (rows.length === 0) {
			error = 'Add at least one choice.';
			return { ok: false, options: null };
		}
		return { ok: true, options: rows };
	}

	function save() {
		error = '';
		if (!label.trim()) return void (error = 'Label is required.');
		if (!key.trim()) return void (error = 'Field key is required.');
		// Field keys must be unique within the form (they key the answers).
		const dupe = siblingFields.some(
			(f) => f.uid !== field?.uid && f.fieldKey.trim() === key.trim()
		);
		if (dupe) {
			return void (error = `Another field already uses the key "${key.trim()}". Keys must be unique.`);
		}
		const opt = buildOptions();
		if (!opt.ok) return;

		// Fold the default value into the validation bag (where defaults live), preserving width and
		// any validation rules; drop the key when blank so an untouched field stays clean.
		const base: FieldValidation = { ...(validation ?? {}) };
		const dv = defaultValue.trim();
		if (dv) base.defaultValue = dv;
		else delete base.defaultValue;
		const mergedValidation = Object.keys(base).length ? base : null;

		const built: FormField = {
			uid: field?.uid ?? `fld-${key.trim()}`,
			fieldKey: key.trim(),
			fieldType,
			label: label.trim(),
			// Section is owned by the builder (which section card the field lives in);
			// preserve any existing value here, the builder re-stamps it on save.
			section: field?.section ?? null,
			description: description.trim() || null,
			required,
			conditions,
			// Width (a drag-set layout hint) + the default value are preserved in the validation bag.
			validation: mergedValidation,
			options: opt.options,
			sortOrder: field?.sortOrder ?? 0
		};
		onsave?.(built);
		open = false;
	}
</script>

<Sheet.Root bind:open>
	<Sheet.Content class="flex w-full flex-col gap-0">
		<Sheet.Header class="border-b px-6 pt-6 pb-4">
			<Sheet.Title>{isEdit ? 'Edit field' : 'Add field'}</Sheet.Title>
			<Sheet.Description
				>Set what to ask for, then add rules only if you need them.</Sheet.Description
			>
		</Sheet.Header>

		<div class="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
			{#if error}
				<div
					class="border-destructive/30 bg-destructive/5 text-destructive flex items-start gap-2 rounded-lg border p-3 text-sm"
				>
					<AlertCircleIcon class="mt-0.5 size-4 shrink-0" />
					<span>{error}</span>
				</div>
			{/if}

			<!-- Basics -->
			<div class="space-y-4">
				<div class="space-y-1.5">
					<Label for="fld-label">Label</Label>
					<Input
						id="fld-label"
						placeholder="e.g. Date of birth"
						bind:value={label}
						oninput={onLabelInput}
					/>
				</div>
				<div class="space-y-1.5">
					<Label for="fld-type">Type</Label>
					<SelectMenu
						id="fld-type"
						size="sm"
						items={FORM_FIELD_TYPES}
						value={fieldType}
						onValueChange={(v) => {
							fieldType = v;
							if (v === 'repeating' && columns.length === 0) columns = [blankColumn()];
						}}
					/>
				</div>
				{#if isDisplay}
					<p
						class="border-info-line bg-info-subtle text-info-strong rounded-md border p-2.5 text-xs"
					>
						A read-only value shown from the record — not an input. The <strong>field key</strong>
						below is the record path to read (e.g. <code>applicant.full_name</code>).
					</p>
				{/if}
				<div class="space-y-1.5">
					<Label for="fld-key">Field key</Label>
					<Input
						id="fld-key"
						class="font-mono text-sm"
						placeholder="e.g. date_of_birth"
						list="standard-field-keys"
						bind:value={key}
						oninput={onKeyInput}
					/>
					<datalist id="standard-field-keys">
						{#each ALL_STANDARD_FIELDS as s (s.key)}
							<option value={s.key}>{s.label}</option>
						{/each}
					</datalist>
					<p class="text-muted-foreground text-xs">
						Where the answer is stored. Pick a standard variable or type your own.
					</p>
				</div>
				{#if !isDisplay}
					<div class="flex items-center gap-2">
						<Switch id="fld-required" bind:checked={required} />
						<Label for="fld-required" class="m-0">Required</Label>
					</div>
				{/if}
				<div class="space-y-1.5">
					<Label for="fld-desc">
						Help text <span class="text-muted-foreground font-normal">(optional)</span>
					</Label>
					<Textarea
						id="fld-desc"
						rows={2}
						placeholder="Shown beneath the field"
						bind:value={description}
					/>
				</div>
				{#if !isDisplay && !isRepeating}
					<div class="space-y-1.5">
						<Label for="fld-default">
							Default value <span class="text-muted-foreground font-normal">(optional)</span>
						</Label>
						<Input
							id="fld-default"
							placeholder="Pre-filled until the user changes it"
							bind:value={defaultValue}
						/>
						<p class="text-muted-foreground text-xs">
							Used when the field is first shown. For a choice field, enter the option's stored
							value.
						</p>
					</div>
				{/if}
			</div>

			{#if isRepeating}
				<div class="space-y-3 border-t pt-4">
					<div class="flex items-center justify-between">
						<Label>Columns</Label>
						<Button
							variant="outline"
							size="sm"
							onclick={() => (columns = [...columns, blankColumn()])}
						>
							<PlusIcon class="mr-1 size-4" /> Add column
						</Button>
					</div>
					<p class="text-muted-foreground text-xs">
						Each row of the answer is one object, keyed by the column key. A column is required
						unless you mark it optional.
					</p>
					{#each columns as column, index (index)}
						<div class="space-y-3 rounded-lg border p-3">
							<div class="grid gap-2 sm:grid-cols-2">
								<div class="space-y-1">
									<Label class="text-muted-foreground text-xs">Label</Label>
									<Input
										placeholder="Provider"
										bind:value={column.label}
										oninput={() => {
											if (!column.keyEdited) column.key = slugifyCode(column.label);
										}}
									/>
								</div>
								<div class="space-y-1">
									<Label class="text-muted-foreground text-xs">Key</Label>
									<Input
										class="font-mono text-sm"
										placeholder="provider"
										bind:value={column.key}
										oninput={() => (column.keyEdited = true)}
									/>
								</div>
								<div class="space-y-1">
									<Label class="text-muted-foreground text-xs">Type</Label>
									<SelectMenu
										size="sm"
										items={REPEATING_COLUMN_TYPES}
										value={column.type}
										onValueChange={(value) => {
											column.type = value;
											if (OPTION_REQUIRED_TYPES.includes(value) && column.choices.length === 0) {
												column.choices = [{ value: '', label: '' }];
											}
										}}
									/>
								</div>
								<div class="flex items-end justify-between gap-2 pb-0.5">
									<label class="flex items-center gap-2 text-sm">
										<Switch bind:checked={column.required} />
										Required
									</label>
									<Button
										variant="ghost"
										size="icon"
										class="size-8"
										aria-label="Remove column"
										onclick={() => (columns = columns.filter((_, i) => i !== index))}
									>
										<Trash2Icon class="size-4" />
									</Button>
								</div>
							</div>
							{#if OPTION_REQUIRED_TYPES.includes(column.type)}
								<div class="space-y-2">
									<p class="text-muted-foreground text-xs">Choices</p>
									{#each column.choices as choice, choiceIndex (choiceIndex)}
										<div class="grid grid-cols-[1fr_1fr_auto] items-center gap-2">
											<Input placeholder="Label" bind:value={choice.label} />
											<Input
												class="font-mono text-sm"
												placeholder="Value"
												bind:value={choice.value}
											/>
											<Button
												variant="ghost"
												size="icon"
												class="size-8"
												aria-label="Remove choice"
												onclick={() => {
													column.choices = column.choices.filter((_, i) => i !== choiceIndex);
												}}
											>
												<Trash2Icon class="size-4" />
											</Button>
										</div>
									{/each}
									<Button
										variant="ghost"
										size="sm"
										onclick={() => (column.choices = [...column.choices, { value: '', label: '' }])}
									>
										<PlusIcon class="mr-1 size-4" /> Add choice
									</Button>
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}

			<!-- Choices (select / combobox / multi-select) -->
			{#if showChoices}
				<div class="space-y-2 border-t pt-4">
					<div class="flex items-center justify-between">
						<Label>Choices</Label>
						<Button
							variant="link"
							size="sm"
							class="text-muted-foreground hover:text-foreground h-auto p-0 text-xs underline"
							onclick={toggleAdvanced}
						>
							{optionsAdvanced ? 'Simple list' : 'Advanced (JSON)'}
						</Button>
					</div>
					{#if optionsAdvanced}
						<Textarea
							bind:value={optionsJson}
							rows={5}
							spellcheck={false}
							placeholder={'[{ "value": "yes", "label": "Yes" }]'}
							class="p-2 font-mono text-xs"
						/>
						<p class="text-muted-foreground text-xs">
							A JSON array of <code>{'{ value, label }'}</code>, or
							<code>{'{ "source": "…" }'}</code> to pull from a data source.
						</p>
					{:else}
						<div class="space-y-2">
							<div class="text-muted-foreground grid grid-cols-[1fr_1fr_auto] gap-2 px-0.5 text-xs">
								<span>Label (shown)</span>
								<span>Value (stored)</span>
								<span class="w-8"></span>
							</div>
							{#each optionRows as row, i (i)}
								<div class="grid grid-cols-[1fr_1fr_auto] items-center gap-2">
									<Input placeholder="Yes" bind:value={row.label} />
									<Input class="font-mono text-sm" placeholder="yes" bind:value={row.value} />
									<Button
										variant="ghost"
										size="icon"
										class="size-8"
										aria-label="Remove choice"
										onclick={() => removeChoice(i)}
									>
										<Trash2Icon class="size-4" />
									</Button>
								</div>
							{/each}
							<Button variant="outline" size="sm" onclick={addChoice}>
								<PlusIcon class="mr-1 size-4" /> Add choice
							</Button>
							<p class="text-muted-foreground text-xs">Leave the value blank to reuse the label.</p>
						</div>
					{/if}
				</div>
			{/if}

			<!-- Currency (money fields) -->
			{#if isCurrency}
				<div class="space-y-1.5 border-t pt-4">
					<Label for="fld-currency"
						>Currency <span class="text-muted-foreground font-normal">(optional)</span></Label
					>
					<Input
						id="fld-currency"
						class="w-32 font-mono text-sm"
						placeholder="GBP"
						bind:value={currencyCode}
					/>
					<p class="text-muted-foreground text-xs">
						ISO code shown as the symbol. Defaults to GBP.
					</p>
				</div>
			{/if}

			<!-- Display condition (collapsible) -->
			<div class="border-t pt-4">
				<button
					type="button"
					class="flex w-full items-center justify-between text-left"
					aria-expanded={showConditions}
					onclick={() => (showConditions = !showConditions)}
				>
					<span class="flex items-center gap-2 text-sm font-medium">
						<EyeIcon class="text-muted-foreground size-4" /> Display condition
						{#if conditions}<StatusBadge tone="brand">On</StatusBadge>{/if}
					</span>
					<ChevronDownIcon
						class={[
							'text-muted-foreground size-4 transition-transform',
							showConditions && 'rotate-180'
						]}
					/>
				</button>
				{#if showConditions}
					<div class="space-y-2 pt-3">
						<ConditionsEditor bind:value={conditions} {availableKeys} />
						{#if conditions}
							<Button
								variant="link"
								size="sm"
								class="text-muted-foreground hover:text-destructive h-auto p-0 text-xs underline"
								onclick={clearConditions}
							>
								Remove condition (always show)
							</Button>
						{/if}
					</div>
				{:else}
					<p class="text-muted-foreground pt-1 text-xs">
						Always visible. Open to show it only when other answers match.
					</p>
				{/if}
			</div>

			<!-- Validation (collapsible) -->
			<div class="border-t pt-4">
				<button
					type="button"
					class="flex w-full items-center justify-between text-left"
					aria-expanded={showValidation}
					onclick={() => (showValidation = !showValidation)}
				>
					<span class="flex items-center gap-2 text-sm font-medium">
						<ShieldCheckIcon class="text-muted-foreground size-4" /> Validation rules
						{#if validation && Object.keys(validation).length}<StatusBadge tone="brand"
								>On</StatusBadge
							>{/if}
					</span>
					<ChevronDownIcon
						class={[
							'text-muted-foreground size-4 transition-transform',
							showValidation && 'rotate-180'
						]}
					/>
				</button>
				{#if showValidation}
					<div class="space-y-2 pt-3">
						<ValidationEditor bind:value={validation} {fieldType} />
						{#if validation && Object.keys(validation).length}
							<Button
								variant="link"
								size="sm"
								class="text-muted-foreground hover:text-destructive h-auto p-0 text-xs underline"
								onclick={clearValidation}
							>
								Clear validation
							</Button>
						{/if}
					</div>
				{:else}
					<p class="text-muted-foreground pt-1 text-xs">
						No extra checks. Open to set length, range or a pattern.
					</p>
				{/if}
			</div>
		</div>

		<div class="flex items-center justify-between border-t px-6 py-4">
			<Button variant="ghost" onclick={() => (open = false)}>Cancel</Button>
			<Button onclick={save}>{isEdit ? 'Save field' : 'Add field'}</Button>
		</div>
	</Sheet.Content>
</Sheet.Root>
