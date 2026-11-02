<!--
	The condition inspector — one question, and the two places the journey can go.

	A condition is a two-armed branch (flow-contract.json → `controlNodes.condition`), so this panel
	is the branch inspector with the arms fixed and named: write the condition, say where the journey
	goes when it holds, and say where it goes when it does not. Both pickers may name the SAME node —
	that is the convergence, and the copy says so, because "otherwise, carry on" is the commonest
	answer and an author should not have to work out that they are allowed to give it.

	It deliberately does not offer "add a path" or "remove a path": three answers is a branch, and the
	panel says which node to reach for rather than growing into one.

	Uses the SAME `ConditionBuilder` as an automation's trigger conditions and a branch's arms, so a
	firm learns one way to express a condition and the three surfaces cannot drift into three dialects.
-->
<script lang="ts">
	import { Input, Label, SelectMenu } from '@graphene/ui';
	import { ConditionBuilder, toJson, toModel, type BuilderGroup } from '@graphene/ui/automations';
	import {
		branchesOf,
		conditionArms,
		conditionArmsOf,
		CONDITION_TRUE,
		type FlowBranch
	} from '@graphene/ui/book/journeys';
	import { nodeLabel, targetId, targetOptions, targetValue } from './shared';
	import type { InspectorProps } from './types';

	let { node, nodes, canManage, onchange, onrestructure }: InspectorProps = $props();

	const targets = $derived(targetOptions(nodes, node.id, 'Nowhere — the journey ends here'));

	// One builder model, seeded from the stored tree and re-seeded when the selection moves to another
	// condition. Held outside the node because the model carries edit-time identity (a row keeps its
	// place while its dropdowns are being changed) that the stored grammar deliberately has no room
	// for.
	let model = $state<BuilderGroup>(toModel(null));
	let seededFor = $state('');
	$effect(() => {
		if (seededFor === node.id) return;
		seededFor = node.id;
		model = toModel(conditionArmsOf(node)?.when.conditions ?? null);
	});

	// A condition arriving from anywhere but the palette (hand-written JSON, an older draft) may not
	// carry the pair. Seeded rather than rendered broken, the way the branch inspector seeds its else:
	// the shape the engine requires should never have to be authored.
	$effect(() => {
		if (!canManage || conditionArmsOf(node)) return;
		const arms = branchesOf(node);
		write(
			conditionArms((arms.find((b) => b.conditions)?.conditions ?? {}) as Record<string, unknown>, {
				when: arms.find((b) => b.conditions)?.next ?? null,
				otherwise: arms.find((b) => !b.conditions)?.next ?? null
			})
		);
		onrestructure();
	});

	function write(arms: FlowBranch[]): void {
		node.config = { ...node.config, branches: arms };
	}

	function patch(id: string, fields: Partial<FlowBranch>, structural = false): void {
		write(branchesOf(node).map((b) => (b.id === id ? { ...b, ...fields } : b)));
		if (structural) onrestructure();
		else onchange();
	}

	function setConditions(next: BuilderGroup): void {
		model = next;
		// `{}` rather than null for a half-written condition: null is what marks the fallback arm, so
		// writing it here would turn the question into a second "otherwise" and the node would always
		// take the first path.
		patch(CONDITION_TRUE, { conditions: toJson(next) ?? {} });
	}

	const arms = $derived(conditionArmsOf(node));
	const converges = $derived(!!arms?.when.next && arms.when.next === arms.otherwise.next);
	const skipped = $derived(nodes.find((n) => n.id === arms?.when.next));
</script>

<div class="space-y-4">
	<div class="space-y-1.5">
		<Label for="condition-title">What this decides</Label>
		<Input
			id="condition-title"
			value={node.title}
			disabled={!canManage}
			placeholder="e.g. An offshore account?"
			oninput={(e) => {
				node.title = e.currentTarget.value;
				onchange();
			}}
		/>
	</div>

	<div class="space-y-2">
		<Label>Only when</Label>
		<ConditionBuilder
			{model}
			disabled={!canManage}
			emptyMessage="No conditions yet, so this decides nothing."
			onchange={setConditions}
		/>
	</div>

	{#if arms}
		<div class="space-y-3 border-t pt-4">
			<div class="space-y-1.5">
				<Label>If it holds, go to</Label>
				<SelectMenu
					size="sm"
					items={targets}
					value={targetValue(arms.when.next)}
					disabled={!canManage}
					onValueChange={(v) => patch(arms.when.id, { next: targetId(v) }, true)}
				/>
			</div>
			<div class="space-y-1.5">
				<Label>Otherwise, go to</Label>
				<SelectMenu
					size="sm"
					items={targets}
					value={targetValue(arms.otherwise.next)}
					disabled={!canManage}
					onValueChange={(v) => patch(arms.otherwise.id, { next: targetId(v) }, true)}
				/>
			</div>
		</div>

		<p class="text-muted-foreground border-t pt-4 text-xs">
			{#if converges}
				Both paths lead to the same place, so this only decides whether <strong class="font-medium"
					>“{nodeLabel(skipped!)}”</strong
				> happens — the journey carries on either way.
			{:else if !arms.otherwise.next}
				Nothing follows the second path, so a case that does not match stops here. Point it at the
				step the journey should carry on with if that is not what you meant.
			{:else}
				The two paths can lead to the same node further down — that is how a journey splits and
				comes back together.
			{/if}
			To offer three or more answers, use a branch.
		</p>
	{/if}
</div>
