<script lang="ts">
	// The authoring side of a block condition, in the PANEL: a modal would cover the block it describes.
	import { Button } from '../../ui/button/index.js';
	import { Input } from '../../ui/input/index.js';
	import Segmented from '../../ui/segmented/segmented.svelte';
	import { SelectMenu, type SelectMenuOption } from '../../ui/select-menu/index.js';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import XIcon from '@lucide/svelte/icons/x';
	import {
		DEFAULT_VISIBILITY,
		describeVisibility,
		OPERATOR_LABELS,
		OPERATORS_WITH_VALUE,
		visibilityExpression,
		type ConditionOp,
		type ConditionRule,
		type Visibility
	} from './model.js';

	let {
		visibility,
		fields,
		disabled = false,
		idPrefix = 'vis',
		onchange
	}: {
		visibility: Visibility | null;
		fields: readonly SelectMenuOption[];
		disabled?: boolean;
		idPrefix?: string;
		/** Null clears the condition entirely. */
		onchange: (next: Visibility | null) => void;
	} = $props();

	const current = $derived(visibility ?? DEFAULT_VISIBILITY);
	const rules = $derived(current.rules);

	const OPERATOR_ITEMS: SelectMenuOption[] = (Object.keys(OPERATOR_LABELS) as ConditionOp[]).map(
		(op) => ({ value: op, label: OPERATOR_LABELS[op] })
	);

	const expression = $derived(visibilityExpression(current));

	function commit(next: Visibility): void {
		// An empty rule list is the ABSENCE of a condition: clearing is deleting the last rule.
		onchange(next.rules.length === 0 ? null : next);
	}

	function addRule(): void {
		const field = fields[0]?.value ?? '';
		commit({ ...current, rules: [...rules, { field, op: 'set' }] });
	}

	function updateRule(index: number, patch: Partial<ConditionRule>): void {
		commit({
			...current,
			rules: rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule))
		});
	}

	function removeRule(index: number): void {
		commit({ ...current, rules: rules.filter((_, i) => i !== index) });
	}
</script>

<div class="flex flex-col gap-1 px-2 pt-1 pb-2">
	{#if rules.length === 0}
		<p class="text-muted-foreground px-0.5 pb-1 text-xs leading-snug">
			Always shown — add a condition to show or hide it.
		</p>
	{:else}
		<!-- ONE CONTAINER FOR THE WHOLE CONDITION, with its own bar. Show/Hide and Match govern the SET,
		     not a single rule, so they sit above the rules rather than as rows among them. -->
		<div class="bg-muted/40 overflow-hidden rounded-md border">
			<div class="bg-background/60 flex items-center gap-2 border-b px-2 py-1.5">
				<Segmented
					size="sm"
					shape="square"
					label="Show or hide"
					{disabled}
					value={current.action}
					onValueChange={(action) =>
						commit({ ...current, action: action === 'hide' ? 'hide' : 'show' })}
					options={[
						{ value: 'show', label: 'Show' },
						{ value: 'hide', label: 'Hide' }
					]}
				/>
				{#if rules.length > 1}
					<span class="text-muted-foreground ml-auto text-[0.65rem]">Match</span>
					<Segmented
						size="sm"
						shape="square"
						label="Match all or any condition"
						{disabled}
						value={current.match}
						onValueChange={(match) =>
							commit({ ...current, match: match === 'any' ? 'any' : 'all' })}
						options={[
							{ value: 'all', label: 'All' },
							{ value: 'any', label: 'Any' }
						]}
					/>
				{/if}
			</div>

			{#each rules as rule, i (i)}
				{@const needsValue = OPERATORS_WITH_VALUE.includes(rule.op)}
				<div class="flex flex-col gap-1.5 border-b p-2 last:border-b-0">
					<div class="flex items-center gap-1.5">
						<SelectMenu
							id={`${idPrefix}-field-${i}`}
							{disabled}
							size="sm"
							class="min-w-0 flex-1"
							value={rule.field}
							items={fields as SelectMenuOption[]}
							placeholder="Choose a field"
							onValueChange={(field) => updateRule(i, { field })}
						/>
						<Button
							variant="ghost"
							size="icon-sm"
							{disabled}
							aria-label={`Remove condition ${i + 1}`}
							title="Remove this condition"
							onclick={() => removeRule(i)}
						>
							<XIcon class="size-3.5" />
						</Button>
					</div>
					<div class="flex items-center gap-1.5">
						<SelectMenu
							id={`${idPrefix}-op-${i}`}
							{disabled}
							size="sm"
							class={needsValue ? 'w-[8.5rem] shrink-0' : 'min-w-0 flex-1'}
							value={rule.op}
							items={OPERATOR_ITEMS}
							onValueChange={(op) =>
								// An empty string, not undefined: undefined means not-yet-typed, and the compiler refuses the rule.
								updateRule(i, {
									op: op as ConditionOp,
									value: OPERATORS_WITH_VALUE.includes(op as ConditionOp)
										? (rule.value ?? '')
										: undefined
								})}
						/>
						{#if needsValue}
							<Input
								id={`${idPrefix}-value-${i}`}
								{disabled}
								class="h-8 min-w-0 flex-1"
								value={rule.value ?? ''}
								placeholder="value"
								spellcheck="false"
								oninput={(e) => updateRule(i, { value: e.currentTarget.value })}
							/>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/if}

	<Button variant="outline" size="sm" class="mt-1 w-full" {disabled} onclick={addRule}>
		<PlusIcon class="mr-1.5 size-3.5" />
		{rules.length === 0 ? 'Add a condition' : 'And another'}
	</Button>

	{#if rules.length > 0}
		<!-- The condition in the author's own words. The compiled `{% if %}` is deliberately NOT shown:
		     this panel exists so an author never has to read the template language. -->
		<p class="text-muted-foreground pt-1.5 text-[0.7rem] leading-snug">
			{describeVisibility(current)}
		</p>
		{#if !expression}
			<p class="text-warning text-[0.7rem] leading-snug">
				Not applied yet — finish the condition above.
			</p>
		{/if}
	{/if}
</div>
