<script lang="ts">
	import type { FieldConditions, SimpleCondition, ConditionOperator } from '@graphene/ui';
	import { CONDITION_OPERATORS, Tabs, Button, Input, SelectMenu, Textarea } from '@graphene/ui';
	import { untrack } from 'svelte';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';

	let {
		value = $bindable(null),
		availableKeys = []
	}: {
		value?: FieldConditions;
		/** sibling field keys a condition can reference. */
		availableKeys?: { value: string; label: string }[];
	} = $props();

	type Row = { field: string; operator: ConditionOperator; value: string };

	const MODE_ITEMS = [
		{ value: 'none', label: 'Always (no condition)' },
		{ value: 'single', label: 'A condition is met' },
		{ value: 'group', label: 'Multiple conditions are met' }
	];
	const LOGIC_ITEMS = [
		{ value: 'AND', label: 'All' },
		{ value: 'OR', label: 'Any' }
	];

	function blankRow(): Row {
		return { field: availableKeys[0]?.value ?? '', operator: 'equals', value: '' };
	}

	function operatorTakesValue(op: ConditionOperator): boolean {
		return CONDITION_OPERATORS.find((o) => o.value === op)?.takesValue ?? true;
	}

	// value → row: an `in`/`notIn` value is an array (comma-joined for the input),
	// everything else is a scalar rendered as a string.
	function toRow(c: SimpleCondition): Row {
		const op = c.operator;
		let v = '';
		if (op === 'in' || op === 'notIn') v = Array.isArray(c.value) ? c.value.join(', ') : '';
		else if (c.value !== undefined && c.value !== null) v = String(c.value);
		return { field: c.field ?? '', operator: op ?? 'equals', value: v };
	}

	// row → condition: parse the input back (comma-split for in/notIn; best-effort
	// JSON otherwise so "5"/"true" become typed), matching platform-1.
	function fromRow(r: Row): SimpleCondition {
		const out: SimpleCondition = { field: r.field, operator: r.operator };
		if (!operatorTakesValue(r.operator)) return out;
		if (r.operator === 'in' || r.operator === 'notIn') {
			out.value = r.value
				.split(',')
				.map((s) => s.trim())
				.filter(Boolean);
		} else {
			try {
				out.value = JSON.parse(r.value);
			} catch {
				out.value = r.value;
			}
		}
		return out;
	}

	function parse(v: FieldConditions): {
		mode: 'none' | 'single' | 'group';
		logic: 'AND' | 'OR';
		rows: Row[];
	} {
		if (!v) return { mode: 'none', logic: 'AND', rows: [blankRow()] };
		const conditional = 'conditional' in v ? v.conditional : (v as SimpleCondition);
		if (conditional && typeof conditional === 'object' && 'conditions' in conditional) {
			return {
				mode: 'group',
				logic: conditional.logic ?? 'AND',
				rows: conditional.conditions.length ? conditional.conditions.map(toRow) : [blankRow()]
			};
		}
		return { mode: 'single', logic: 'AND', rows: [toRow(conditional as SimpleCondition)] };
	}

	const initial = untrack(() => parse(value));
	let mode = $state(initial.mode);
	let logic = $state<'AND' | 'OR'>(initial.logic);
	let rows = $state<Row[]>(initial.rows);
	let tab = $state('builder');
	let jsonText = $state(JSON.stringify(value ?? null, null, 2));
	let jsonError = $state('');

	function emit() {
		let next: FieldConditions;
		if (mode === 'none') next = null;
		else if (mode === 'group') next = { conditional: { logic, conditions: rows.map(fromRow) } };
		else next = { conditional: fromRow(rows[0] ?? blankRow()) };
		value = next;
		jsonText = JSON.stringify(next ?? null, null, 2);
		jsonError = '';
	}

	function applyJson() {
		try {
			const parsed = jsonText.trim() ? JSON.parse(jsonText) : null;
			value = parsed as FieldConditions;
			const re = parse(value);
			mode = re.mode;
			logic = re.logic;
			rows = re.rows;
			jsonError = '';
		} catch {
			jsonError = 'Invalid JSON.';
		}
	}

	function addRow() {
		rows = [...rows, blankRow()];
		emit();
	}
	function removeRow(i: number) {
		rows = rows.filter((_, idx) => idx !== i);
		if (!rows.length) rows = [blankRow()];
		emit();
	}
</script>

<Tabs.Root bind:value={tab}>
	<Tabs.List class="grid w-full grid-cols-2">
		<Tabs.Trigger value="builder">Builder</Tabs.Trigger>
		<Tabs.Trigger value="json">JSON</Tabs.Trigger>
	</Tabs.List>

	<Tabs.Content value="builder" class="space-y-3 pt-3">
		<div class="space-y-1.5">
			<span class="text-sm font-medium">Show this field when…</span>
			<SelectMenu
				size="sm"
				items={MODE_ITEMS}
				value={mode}
				onValueChange={(v) => {
					mode = v as typeof mode;
					emit();
				}}
			/>
		</div>

		{#if mode === 'group'}
			<div class="flex items-center gap-2 text-sm">
				<span class="text-muted-foreground">Match</span>
				<SelectMenu
					size="sm"
					class="w-20 shrink-0"
					items={LOGIC_ITEMS}
					value={logic}
					onValueChange={(v) => {
						logic = v as 'AND' | 'OR';
						emit();
					}}
				/>
				<span class="text-muted-foreground">of the following</span>
			</div>
		{/if}

		{#if mode !== 'none'}
			<div class="space-y-2">
				{#each rows as row, i (i)}
					<div class="space-y-2 rounded-lg border p-2.5">
						<div class="flex items-center gap-2">
							<SelectMenu
								size="sm"
								class="min-w-0 flex-1"
								items={availableKeys.length
									? availableKeys
									: [{ value: '', label: '(no other fields)' }]}
								value={row.field}
								onValueChange={(v) => {
									row.field = v;
									emit();
								}}
							/>
							{#if mode === 'group'}
								<Button
									variant="ghost"
									size="icon"
									class="size-8 shrink-0"
									aria-label="Remove condition"
									onclick={() => removeRow(i)}
								>
									<Trash2Icon class="size-4" />
								</Button>
							{/if}
						</div>
						<div class="flex items-center gap-2">
							<SelectMenu
								size="sm"
								class="w-44 shrink-0"
								items={CONDITION_OPERATORS}
								value={row.operator}
								onValueChange={(v) => {
									row.operator = v as ConditionOperator;
									emit();
								}}
							/>
							{#if operatorTakesValue(row.operator)}
								<Input
									class="h-8 flex-1"
									placeholder={row.operator === 'in' || row.operator === 'notIn'
										? 'a, b, c'
										: 'value'}
									bind:value={row.value}
									oninput={emit}
								/>
							{/if}
						</div>
					</div>
				{/each}
				{#if mode === 'group'}
					<Button variant="outline" size="sm" onclick={addRow}>
						<PlusIcon class="mr-1 size-4" /> Add condition
					</Button>
				{/if}
			</div>
		{/if}
	</Tabs.Content>

	<Tabs.Content value="json" class="space-y-2 pt-3">
		<Textarea
			bind:value={jsonText}
			oninput={applyJson}
			rows={6}
			spellcheck={false}
			class="p-2 font-mono text-xs"
			aria-invalid={Boolean(jsonError)}
		/>
		{#if jsonError}<p class="text-destructive text-xs">{jsonError}</p>{/if}
		<p class="text-muted-foreground text-xs">
			Stored as <code>{'{ conditional: … }'}</code> (or null). Visibility belongs here, not in Validation.
		</p>
	</Tabs.Content>
</Tabs.Root>
