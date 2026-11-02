<script lang="ts">
	// The per-condition verdict tree — "which row is wrong, and what did it actually read".
	//
	// Extracted out of `dry-run-panel.svelte` when the flow tier's dry run needed the identical
	// rendering for a branch's arms and a step's plan-time visibility. It has to be identical:
	// `conditions.explain` on the server produces ONE shape for all three sites precisely so an
	// author reads the same thing wherever a condition is judged, and two renderers of one shape
	// would drift the moment either got a tweak.
	//
	// It renders a verdict, never computes one. The `passed` on every node came from the same
	// evaluator the engine runs; a browser-side re-evaluation would be a second semantics.
	import CheckIcon from '@lucide/svelte/icons/check';
	import XIcon from '@lucide/svelte/icons/x';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import { cn } from '../../utils.js';
	import { fieldLabel } from './summary.js';
	import { OPERATOR_SPECS } from './contract.js';
	import type { ExplainNode } from './types.js';
	// Self-import rather than `<svelte:self>`: the element is deprecated in Svelte 5 and a
	// component importing itself by path is the supported way to recurse.
	import Self from './explain-tree.svelte';

	let {
		node,
		depth = 0,
		/** Copy for the `always` case. A rule with no conditions and a branch arm with none are the
		 *  same structure but not the same sentence. */
		alwaysLabel = 'No conditions — this always applies.'
	}: {
		node: ExplainNode;
		depth?: number;
		alwaysLabel?: string;
	} = $props();

	function display(value: unknown): string {
		if (value === null || value === undefined) return '(nothing)';
		if (Array.isArray(value)) return value.length ? value.join(', ') : '(empty)';
		if (value === '') return '(empty)';
		return String(value);
	}

	function clause(leaf: Extract<ExplainNode, { kind: 'leaf' }>): string {
		const spec = OPERATOR_SPECS[String(leaf.operator)];
		const verb = spec?.label ?? String(leaf.operator);
		const negated = spec?.negate ? ' (not)' : '';
		return `${fieldLabel(String(leaf.field ?? ''))} ${verb}${negated} ${display(leaf.value)}`.trim();
	}
</script>

{#if node.kind === 'always'}
	<p class="text-muted-foreground text-sm">{alwaysLabel}</p>
{:else if node.kind === 'group'}
	<div class={cn('space-y-1', depth > 0 && 'border-border ml-3 border-l pl-3')}>
		{#if node.conditions.length > 1}
			<p class="text-muted-foreground text-xs">
				Match {node.logic === 'or' ? 'any' : 'all'} of these
				<StatusBadge tone={node.passed ? 'success' : 'neutral'}>
					{node.passed ? 'held' : 'did not hold'}
				</StatusBadge>
			</p>
		{/if}
		{#each node.conditions as child, index (index)}
			<Self node={child} depth={depth + 1} {alwaysLabel} />
		{/each}
	</div>
{:else}
	<div class="flex items-start gap-2 py-1 text-sm">
		{#if node.passed}
			<CheckIcon class="text-success-strong mt-0.5 size-4 shrink-0" />
		{:else}
			<XIcon class="text-destructive mt-0.5 size-4 shrink-0" />
		{/if}
		<span class="min-w-0">
			{clause(node)}
			<span class="text-muted-foreground">— actual: {display(node.actual)}</span>
		</span>
	</div>
{/if}
