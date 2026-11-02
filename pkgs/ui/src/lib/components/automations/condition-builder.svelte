<script lang="ts">
	// The condition builder: rows of FIELD · TEST · VALUE, with AND/OR grouping and the is/is-not
	// polarity the operators encode.
	//
	// Descriptor-driven, like the client-portal block inspector: the field picker, the test list and
	// the value control are all derived from flow-contract.json (`./contract.ts`), never hand-built
	// per field. That is what makes "add a field scope to the contract and it appears here" true —
	// and it is why this component contains no knowledge of what `contact.service_type` means.
	//
	// The parent owns the model; every edit hands back a whole new tree (`./conditions.ts` mutations
	// are pure — these models live in `$state`, where `structuredClone` throws).
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { Button } from '../ui/button/index.js';
	import { Input } from '../ui/input/index.js';
	import { SelectMenu } from '../ui/select-menu/index.js';
	import Segmented from '../ui/segmented/segmented.svelte';
	import { cn } from '../../utils.js';
	import { sentenceCase } from '../../sentence-case.js';
	import { BASE_OPERATORS, FIELDS, OPERATOR_SPECS } from './contract.js';
	import {
		addChild,
		blankGroup,
		blankLeaf,
		removeNode,
		setOperator,
		togglePolarity,
		updateNode,
		type BuilderGroup,
		type BuilderLeaf
	} from './conditions.js';
	import { fieldLabel } from './summary.js';
	// Svelte 5 self-reference: a component may import itself by path (svelte:self is retired).
	import Self from './condition-builder.svelte';

	let {
		model,
		onchange,
		disabled = false,
		/** Extra field paths to offer beyond the closed registry — e.g. memory keys already in use. */
		extraFields = [],
		/**
		 * What "no conditions" means HERE. The default is the rule tier's reading; a branch arm and a
		 * mid-flow listener mean something different by an empty tree, and a builder that tells an
		 * author their branch "fires every time the trigger happens" is describing another screen.
		 */
		emptyMessage = 'No conditions — this automation fires every time the trigger happens.',
		/** Nesting depth; the root renders without its own frame. */
		depth = 0,
		group = undefined
	}: {
		model: BuilderGroup;
		onchange: (next: BuilderGroup) => void;
		disabled?: boolean;
		extraFields?: string[];
		emptyMessage?: string;
		depth?: number;
		/** The sub-group this instance renders; defaults to `model` (the root). */
		group?: BuilderGroup;
	} = $props();

	const node = $derived(group ?? model);

	// The field picker's options. The closed registry, plus anything already authored (a memory key
	// the firm invented must not disappear from its own dropdown), plus a free-text escape for the
	// open `memory.*` / `event.subject.*` namespaces.
	const fieldOptions = $derived(
		[...new Set([...FIELDS, ...extraFields])].map((f) => ({ value: f, label: fieldLabel(f) }))
	);

	const testOptions = BASE_OPERATORS.map((spec) => ({
		value: spec.op,
		label: sentenceCase(spec.label)
	}));

	const POLARITY = [
		{ value: 'is', label: 'Is' },
		{ value: 'not', label: 'Is not' }
	];

	function edit(next: BuilderGroup) {
		onchange(next);
	}

	function valueKind(leaf: BuilderLeaf) {
		return OPERATOR_SPECS[leaf.op]?.value ?? 'text';
	}

	function hasPolarity(leaf: BuilderLeaf) {
		return Boolean(OPERATOR_SPECS[leaf.op]?.twin);
	}

	function placeholderFor(leaf: BuilderLeaf): string {
		switch (valueKind(leaf)) {
			case 'list':
				return 'individual, organisation';
			case 'days':
				return 'days';
			case 'number':
				return '0';
			case 'date':
				return 'YYYY-MM-DD';
			default:
				return 'value';
		}
	}
</script>

<div
	class={cn(
		'space-y-2',
		depth > 0 && 'border-border bg-muted/30 rounded-md border border-dashed p-3'
	)}
>
	{#if node.children.length > 1}
		<!-- The joiner sits ABOVE the rows it joins, once, rather than repeating between each pair:
		     a group has ONE logic, and rendering it per gap invites the reading that each gap can
		     differ (nested groups are how you actually express that). -->
		<div class="flex items-center gap-2">
			<span class="text-muted-foreground text-xs">Match</span>
			<Segmented
				options={[
					{ value: 'and', label: 'All' },
					{ value: 'or', label: 'Any' }
				]}
				value={node.logic}
				{disabled}
				onValueChange={(v) =>
					edit(updateNode(model, node.id, { logic: v === 'or' ? 'or' : 'and' }))}
			/>
			<span class="text-muted-foreground text-xs">of these</span>
		</div>
	{/if}

	{#each node.children as child (child.id)}
		{#if child.kind === 'group'}
			<div class="flex items-start gap-2">
				<div class="min-w-0 flex-1">
					<Self
						{model}
						{onchange}
						{disabled}
						{extraFields}
						{emptyMessage}
						depth={depth + 1}
						group={child}
					/>
				</div>
				{#if !disabled}
					<Button
						type="button"
						variant="ghost"
						size="icon"
						aria-label="Remove group"
						onclick={() => edit(removeNode(model, child.id))}
					>
						<Trash2Icon class="size-4" />
					</Button>
				{/if}
			</div>
		{:else}
			{@const leaf = child as BuilderLeaf}
			<div class="flex flex-wrap items-center gap-2" data-condition-row={leaf.id}>
				<div class="min-w-[12rem] flex-1">
					<!-- The field is a list-backed text input rather than a hard dropdown: the closed
					     registry is offered as options, and the open memory.*/event.subject.* namespaces
					     stay typeable. A dropdown here would make an authored memory key unreachable. -->
					<Input
						list="automation-fields"
						value={leaf.field}
						{disabled}
						placeholder="Field"
						aria-label="Field"
						oninput={(e) => edit(updateNode(model, leaf.id, { field: e.currentTarget.value }))}
					/>
				</div>
				{#if hasPolarity(leaf)}
					<Segmented
						options={POLARITY}
						value={leaf.negate ? 'not' : 'is'}
						{disabled}
						onValueChange={() => edit(togglePolarity(model, leaf.id))}
					/>
				{/if}
				<div class="min-w-[9rem]">
					<SelectMenu
						items={testOptions}
						value={leaf.op}
						{disabled}
						size="sm"
						placeholder="Test"
						onValueChange={(op) => edit(setOperator(model, leaf.id, op))}
					/>
				</div>
				{#if valueKind(leaf) !== 'none'}
					<div class="min-w-[10rem] flex-1">
						<Input
							value={leaf.value}
							{disabled}
							placeholder={placeholderFor(leaf)}
							aria-label="Value"
							oninput={(e) => edit(updateNode(model, leaf.id, { value: e.currentTarget.value }))}
						/>
					</div>
				{/if}
				{#if !disabled}
					<Button
						type="button"
						variant="ghost"
						size="icon"
						aria-label="Remove condition"
						onclick={() => edit(removeNode(model, leaf.id))}
					>
						<Trash2Icon class="size-4" />
					</Button>
				{/if}
			</div>
		{/if}
	{/each}

	{#if node.children.length === 0}
		<p class="text-muted-foreground text-sm">{emptyMessage}</p>
	{/if}

	{#if !disabled}
		<div class="flex gap-2">
			<Button
				type="button"
				variant="outline"
				size="sm"
				onclick={() => edit(addChild(model, node.id, blankLeaf()))}
			>
				<PlusIcon class="size-4" /> Condition
			</Button>
			{#if depth < 2}
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onclick={() =>
						edit(addChild(model, node.id, blankGroup(node.logic === 'and' ? 'or' : 'and')))}
				>
					<PlusIcon class="size-4" /> Group
				</Button>
			{/if}
		</div>
	{/if}
</div>

{#if depth === 0}
	<!-- One datalist for every row's field input, emitted once at the root. -->
	<datalist id="automation-fields">
		{#each fieldOptions as option (option.value)}
			<option value={option.value}>{option.label}</option>
		{/each}
	</datalist>
{/if}
