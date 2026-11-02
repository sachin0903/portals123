<!--
	Dispatches a selected node to the inspector for its kind.

	Deliberately does NOT handle `kind: 'step'` — a step's inspector is the long-standing
	`StepInspector`, which also owns the panel chrome (delete, close) and the step-only concerns
	(assignee, scope, per-participant fan-out, raise-a-task). The canvas picks: a step goes to
	StepInspector, everything else comes here. Folding the two together is a later tidy-up, not a
	prerequisite, and doing it under Phase C would have meant rewriting the one inspector that
	already works while the rest were being written.
-->
<script lang="ts">
	import { Alert } from '@graphene/ui';
	import ActionInspector from './action.svelte';
	import BranchInspector from './branch.svelte';
	import ConditionInspector from './condition.svelte';
	import ExitInspector from './exit.svelte';
	import RawConfig from './raw-config.svelte';
	import StartFlowInspector from './start-flow.svelte';
	import StartInspector from './start.svelte';
	import WaitForEventInspector from './wait-for-event.svelte';
	import WaitInspector from './wait.svelte';
	import type { InspectorProps } from './types';

	// Destructured rather than kept as one `props` object: a non-destructured `$props()` trips
	// `svelte/valid-compile` (Svelte cannot infer what a custom element would expose), and naming
	// them here is also what makes the forwarded set explicit rather than "whatever came in".
	let {
		node,
		nodes,
		forms,
		documents,
		templates,
		roles = [],
		flows = [],
		issues = [],
		canManage,
		onchange,
		onrestructure
	}: InspectorProps = $props();

	const BY_KIND = {
		start: StartInspector,
		action: ActionInspector,
		condition: ConditionInspector,
		branch: BranchInspector,
		wait: WaitInspector,
		wait_for_event: WaitForEventInspector,
		start_flow: StartFlowInspector,
		exit: ExitInspector
	} as const;

	const Inspector = $derived(BY_KIND[node.kind as keyof typeof BY_KIND] ?? RawConfig);
	const forwarded = $derived({
		node,
		nodes,
		forms,
		documents,
		templates,
		roles,
		flows,
		issues,
		canManage,
		onchange,
		onrestructure
	});
</script>

{#if issues.length}
	<Alert variant="destructive" class="mb-4">
		<ul class="list-inside list-disc space-y-1 text-sm">
			{#each issues as issue (issue)}
				<li>{issue}</li>
			{/each}
		</ul>
	</Alert>
{/if}

<Inspector {...forwarded} />
