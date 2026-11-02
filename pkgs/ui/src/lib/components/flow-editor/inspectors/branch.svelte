<!--
	The branch inspector — an ordered list of arms, first satisfiable wins, with a mandatory
	unconditional fallback.

	Three decisions worth stating, because each one is the difference between a control that reads
	like a process and a control that reads like JSON:

	1. **Order is the semantics, so the list is numbered and reads as prose.** "1st · if", "2nd ·
	   otherwise if", "Otherwise". A set of unordered rule cards would hide the single most important
	   thing about a branch: that the first match wins and everything below it is only reached
	   because the ones above did not.

	2. **The “otherwise” arm cannot be authored away.** It is seeded the moment the node is opened,
	   it is pinned last, and it has no delete control. The publish validator refuses a branch
	   without one — a fan-out with no fallback is how a client silently falls off the end of a
	   process — and a refusal at publish is a worse place to learn that than a form that never let
	   it happen.

	3. **Structure vs contents.** Adding, removing, reordering an arm or re-pointing its target is
	   `onrestructure` (the canvas re-derives edges and re-lays-out); typing a label or editing a
	   condition is `onchange`. Calling the wrong one leaves a re-wired edge invisible until reload.
-->
<script lang="ts">
	import { Alert, AlertDescription, Button, Input, Label, SelectMenu } from '@graphene/ui';
	import {
		ConditionBuilder,
		emptyRoot,
		toJson,
		toModel,
		type BuilderGroup
	} from '@graphene/ui/automations';
	import { branchesOf, FLOW_CAPS, type FlowBranch } from '@graphene/ui/book/journeys';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronUpIcon from '@lucide/svelte/icons/chevron-up';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import type { InspectorProps } from './types';
	import { branchOrdinal, targetId, targetOptions, targetValue } from './shared';

	let { node, nodes, canManage, onchange, onrestructure }: InspectorProps = $props();

	const MAX_ARMS = FLOW_CAPS.maxBranchesPerNode ?? 8;

	const arms = $derived(branchesOf(node));
	const targets = $derived(targetOptions(nodes, node.id, 'Nowhere yet'));

	function write(next: FlowBranch[]): void {
		node.config = { ...node.config, branches: next };
	}

	function nextArmId(existing: FlowBranch[]): string {
		const taken = new Set(existing.map((b) => b.id));
		let n = existing.length + 1;
		while (taken.has(`b${n}`)) n += 1;
		return `b${n}`;
	}

	function blankArm(existing: FlowBranch[]): FlowBranch {
		return { id: nextArmId(existing), label: '', conditions: {}, next: null };
	}

	// ── Seeding ──────────────────────────────────────────────────────────────
	// A branch node arrives from the palette with an empty config. Rather than render a control that
	// is already unpublishable, seed the shape the engine requires: one condition to write, and the
	// fallback that cannot be authored away. Idempotent, so re-selecting the node changes nothing.
	let seededFor = $state('');
	$effect(() => {
		if (seededFor === node.id || !canManage) return;
		seededFor = node.id;
		const current = branchesOf(node);
		if (current.some((b) => b.conditions === null)) return;
		write([
			...(current.length ? current : [blankArm([])]),
			{ id: nextArmId(current), label: 'Otherwise', conditions: null, next: null }
		]);
		onrestructure();
	});

	// ── The condition models ─────────────────────────────────────────────────
	// One builder model per arm, seeded from the stored tree and keyed on the arm's id. Held outside
	// the node because the model carries edit-time identity (a row keeps its place while its
	// dropdowns are being changed) that the stored grammar deliberately has no room for.
	let models = $state<Record<string, BuilderGroup>>({});
	let modelsFor = $state('');
	$effect(() => {
		const current = branchesOf(node);
		if (modelsFor !== node.id) {
			modelsFor = node.id;
			models = Object.fromEntries(current.map((b) => [b.id, toModel(b.conditions ?? null)]));
			return;
		}
		// A newly added arm needs a model too. Filled in HERE rather than lazily in the template:
		// writing `$state` while rendering is how a component ends up re-rendering itself.
		const missing = current.filter((b) => !(b.id in models));
		if (missing.length) {
			models = {
				...models,
				...Object.fromEntries(missing.map((b) => [b.id, toModel(b.conditions ?? null)]))
			};
		}
	});

	/** The model for an arm. `emptyRoot()` covers the single render between an arm being added and
	 *  the effect above seeding it — the builder holds no state of its own, so nothing is lost. */
	function modelFor(arm: FlowBranch): BuilderGroup {
		return models[arm.id] ?? emptyRoot();
	}

	function setConditions(arm: FlowBranch, next: BuilderGroup): void {
		models = { ...models, [arm.id]: next };
		// `toJson` returns null for a tree with nothing complete in it — and null means "the else".
		// `{}` keeps the arm CONDITIONAL while the author is still writing the condition, so a
		// half-typed row cannot silently turn an arm into a second fallback.
		patch(arm.id, { conditions: toJson(next) ?? {} });
	}

	// ── Mutations ────────────────────────────────────────────────────────────
	function patch(id: string, fields: Partial<FlowBranch>, structural = false): void {
		write(arms.map((b) => (b.id === id ? { ...b, ...fields } : b)));
		if (structural) onrestructure();
		else onchange();
	}

	function addArm(): void {
		const conditional = arms.filter((b) => b.conditions !== null);
		const fallback = arms.filter((b) => b.conditions === null);
		write([...conditional, blankArm(arms), ...fallback]);
		onrestructure();
	}

	function removeArm(id: string): void {
		write(arms.filter((b) => b.id !== id));
		onrestructure();
	}

	/** Move a conditional arm one place up or down among the conditional arms. The fallback never
	 *  moves: it is last by definition, whatever the array says. */
	function moveArm(id: string, delta: -1 | 1): void {
		const conditional = arms.filter((b) => b.conditions !== null);
		const fallback = arms.filter((b) => b.conditions === null);
		const from = conditional.findIndex((b) => b.id === id);
		const to = from + delta;
		if (from < 0 || to < 0 || to >= conditional.length) return;
		const reordered = [...conditional];
		const [moved] = reordered.splice(from, 1);
		if (!moved) return;
		reordered.splice(to, 0, moved);
		write([...reordered, ...fallback]);
		onrestructure();
	}

	// The rendered order — conditional arms as authored, then the fallback. A stored definition with
	// the fallback in the middle behaves as if it were last (the engine consults it only after the
	// ordered pass), so showing it anywhere else would be a lie about what runs.
	const ordered = $derived([
		...arms.filter((b) => b.conditions !== null),
		...arms.filter((b) => b.conditions === null)
	]);
	const conditionalCount = $derived(arms.filter((b) => b.conditions !== null).length);
	const atCap = $derived(arms.length >= MAX_ARMS);
</script>

<div class="space-y-4">
	<div class="space-y-1.5">
		<Label for="branch-title">What this decides</Label>
		<Input
			id="branch-title"
			value={node.title}
			disabled={!canManage}
			placeholder="e.g. Advised or execution-only?"
			oninput={(e) => {
				node.title = e.currentTarget.value;
				onchange();
			}}
		/>
		<p class="text-muted-foreground text-xs">
			The journey takes the first path whose conditions hold. Nothing below a path that matches is
			considered.
		</p>
	</div>

	<div class="space-y-3">
		{#each ordered as arm, i (arm.id)}
			{@const isElse = arm.conditions === null}
			{@const position = isElse ? -1 : i}
			<div class="space-y-2 rounded-lg border p-3" data-branch-arm={arm.id}>
				<div class="flex items-center gap-2">
					<span class="text-muted-foreground shrink-0 text-xs font-medium">
						{branchOrdinal(position, isElse)}
					</span>
					<Input
						class="h-8 flex-1 text-sm"
						value={arm.label}
						disabled={!canManage}
						placeholder={isElse ? 'Otherwise' : 'Name this path'}
						aria-label="Path name"
						oninput={(e) => patch(arm.id, { label: e.currentTarget.value })}
					/>
					{#if canManage && !isElse}
						<Button
							variant="ghost"
							size="icon-xs"
							class="text-muted-foreground shrink-0"
							aria-label="Move up"
							disabled={position === 0}
							onclick={() => moveArm(arm.id, -1)}
						>
							<ChevronUpIcon class="size-3.5" />
						</Button>
						<Button
							variant="ghost"
							size="icon-xs"
							class="text-muted-foreground shrink-0"
							aria-label="Move down"
							disabled={position >= conditionalCount - 1}
							onclick={() => moveArm(arm.id, 1)}
						>
							<ChevronDownIcon class="size-3.5" />
						</Button>
						<Button
							variant="ghost"
							size="icon-xs"
							class="text-muted-foreground hover:text-destructive shrink-0"
							aria-label="Remove path"
							disabled={conditionalCount <= 1}
							onclick={() => removeArm(arm.id)}
						>
							<Trash2Icon class="size-3.5" />
						</Button>
					{/if}
				</div>

				{#if isElse}
					<p class="text-muted-foreground text-xs">
						Every case that matched none of the paths above comes here. Required — without it a
						client who matches nothing stops dead.
					</p>
				{:else}
					<ConditionBuilder
						model={modelFor(arm)}
						disabled={!canManage}
						emptyMessage="No conditions yet."
						onchange={(next) => setConditions(arm, next)}
					/>
					{#if !toJson(modelFor(arm))}
						<p class="text-destructive text-xs">
							No conditions yet, so this path would catch every case and nothing below it could ever
							run.
						</p>
					{/if}
				{/if}

				<div class="flex items-center gap-2">
					<span class="text-muted-foreground shrink-0 text-xs">then go to</span>
					<SelectMenu
						size="sm"
						class="flex-1"
						items={targets}
						value={targetValue(arm.next)}
						disabled={!canManage}
						onValueChange={(v) => patch(arm.id, { next: targetId(v) }, true)}
					/>
				</div>
				{#if !arm.next}
					<p class="text-muted-foreground/70 text-xs">
						This path leads nowhere yet — the journey would stop here.
					</p>
				{/if}
			</div>
		{/each}
	</div>

	{#if canManage}
		<div class="space-y-2">
			<Button variant="outline" size="sm" disabled={atCap} onclick={addArm}>
				<PlusIcon class="size-4" /> Add a path
			</Button>
			{#if atCap}
				<Alert>
					<AlertDescription>
						A branch takes at most {MAX_ARMS} paths. Send one of them to a second branch instead — it
						reads better than eight conditions on one screen.
					</AlertDescription>
				</Alert>
			{/if}
		</div>
	{/if}
</div>
