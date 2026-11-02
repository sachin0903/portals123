<!--
	The wait-for-event inspector — the listener with a deadline.

	The contract's own example is the one this screen has to make effortless: "no signed form in 7
	days → chase". So it reads top to bottom as that sentence — wait for THIS, only if THESE hold,
	continue to X, and if it has not happened in N days go to Y instead — rather than as four
	unrelated fields.

	The deadline and its branch are one control, not two, because either alone is a node the engine
	complains about: a branch with no deadline can never be taken. Toggling the deadline off clears
	both keys rather than leaving an orphan behind.

	A trigger this node could never be released by is shown DISABLED with its reason rather than
	hidden. Hiding it makes the picker look broken next to the contract; letting it be chosen makes
	a case that waits for ever for an event nobody will send — the failure with no symptom.

	The predicate is `listenerRefusal`, NOT the rule tier's `isSupported`. They are different
	questions: `isSupported` asks whether a trigger can ARM an automation, and by that measure a
	schedule qualifies — but a schedule is not an event, so nothing can ever release a case parked
	waiting for one. Using the rule-tier answer here is how `schedule.recurring` came to be offered
	as something to wait for.
-->
<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Checkbox,
		Input,
		Label,
		SelectMenu,
		StatusBadge
	} from '@graphene/ui';
	import {
		ConditionBuilder,
		listenerRefusal,
		toJson,
		toModel,
		triggerGroups,
		TRIGGER_BY_KEY,
		type BuilderGroup
	} from '@graphene/ui/automations';
	import { FLOW_CAPS } from '@graphene/ui/book/journeys';
	import type { InspectorProps } from './types';
	import NextTargets from './next-targets.svelte';
	import { cfgString, setCfgNumber, targetId, targetOptions, targetValue } from './shared';

	let { node, nodes, canManage, onchange, onrestructure }: InspectorProps = $props();

	const MAX_WAIT_DAYS = FLOW_CAPS.maxWaitDays ?? 90;

	const triggerItems = triggerGroups().map((group) => ({
		label: group.label,
		options: group.triggers.map((t) => ({
			value: t.key,
			label: listenerRefusal(t.key) === null ? t.label : `${t.label} — can’t be waited for`,
			// Disabled, not hidden: a flow that waits for a signal nothing delivers waits for ever.
			disabled: listenerRefusal(t.key) !== null
		}))
	}));

	const triggerKey = $derived(cfgString(node, 'trigger'));
	const spec = $derived(TRIGGER_BY_KEY[triggerKey]);
	const refusal = $derived(triggerKey ? listenerRefusal(triggerKey) : null);
	const targets = $derived(targetOptions(nodes, node.id, 'Nowhere yet'));

	const timeoutDays = $derived(cfgString(node, 'timeout_days'));
	const hasDeadline = $derived(timeoutDays !== '');
	const timeoutBranch = $derived(cfgString(node, 'timeout_branch'));

	// ── Conditions ───────────────────────────────────────────────────────────
	let model = $state<BuilderGroup>(toModel(null));
	let modelFor = $state('');
	$effect(() => {
		if (modelFor === node.id) return;
		modelFor = node.id;
		model = toModel((node.config?.conditions as Record<string, unknown> | null) ?? null);
	});

	function setConditions(next: BuilderGroup): void {
		model = next;
		const json = toJson(next);
		if (json === null) delete node.config.conditions;
		else node.config.conditions = json;
		onchange();
	}

	function toggleDeadline(on: boolean): void {
		if (on) {
			node.config.timeout_days = 7;
		} else {
			delete node.config.timeout_days;
			delete node.config.timeout_branch;
		}
		// The timeout branch is an edge, so turning the deadline off removes one from the canvas.
		onrestructure();
	}
</script>

<div class="space-y-4">
	<div class="space-y-1.5">
		<Label for="wfe-title">What this is waiting for</Label>
		<Input
			id="wfe-title"
			value={node.title}
			disabled={!canManage}
			placeholder="e.g. Signed suitability report"
			oninput={(e) => {
				node.title = e.currentTarget.value;
				onchange();
			}}
		/>
	</div>

	<div class="space-y-1.5">
		<Label for="wfe-trigger">Wait until</Label>
		<SelectMenu
			id="wfe-trigger"
			size="sm"
			items={triggerItems}
			value={triggerKey}
			placeholder="Choose an event…"
			disabled={!canManage}
			onValueChange={(v) => {
				node.config.trigger = v;
				onchange();
			}}
		/>
		{#if spec && refusal === null}
			<p class="text-muted-foreground text-xs">Fires for the journey’s {spec.subject}.</p>
		{:else if spec}
			<p class="text-muted-foreground text-xs">{spec.label}</p>
		{:else if triggerKey}
			<Alert variant="destructive">
				<AlertDescription>
					“{triggerKey}” isn’t an event this engine knows, so the journey would wait here for ever.
					Choose one from the list.
				</AlertDescription>
			</Alert>
		{/if}
	</div>

	<div class="space-y-1.5">
		<Label>Only if</Label>
		<ConditionBuilder
			{model}
			disabled={!canManage}
			emptyMessage="No conditions — the first time this event happens for the case, the journey moves on."
			onchange={setConditions}
		/>
		<p class="text-muted-foreground text-xs">
			The event still has to happen; these narrow which one counts. Leave empty to take the first.
		</p>
	</div>

	<NextTargets {node} {nodes} {canManage} {onrestructure} label="When it happens, go to" />

	<div class="space-y-2 border-t pt-4">
		<div class="flex items-center gap-2">
			<Checkbox
				id="wfe-deadline"
				checked={hasDeadline}
				disabled={!canManage}
				onCheckedChange={(v) => toggleDeadline(v === true)}
			/>
			<Label for="wfe-deadline" class="font-normal">Give up after a while</Label>
		</div>

		{#if hasDeadline}
			<div class="flex items-center gap-2">
				<span class="text-muted-foreground shrink-0 text-xs">If it hasn’t happened in</span>
				<Input
					class="w-20"
					type="number"
					min="1"
					max={String(MAX_WAIT_DAYS)}
					value={timeoutDays}
					disabled={!canManage}
					aria-label="Days before giving up"
					oninput={(e) => {
						setCfgNumber(node, 'timeout_days', e.currentTarget.value);
						onchange();
					}}
				/>
				<span class="text-muted-foreground shrink-0 text-xs">days, go to</span>
			</div>
			<SelectMenu
				size="sm"
				items={targets}
				value={targetValue(timeoutBranch || null)}
				disabled={!canManage}
				onValueChange={(v) => {
					const id = targetId(v);
					if (id === null) delete node.config.timeout_branch;
					else node.config.timeout_branch = id;
					onrestructure();
				}}
			/>
			{#if !timeoutBranch}
				<p class="text-muted-foreground/70 text-xs">
					Pick where a case goes when the deadline passes — a chaser task, an email, an end.
				</p>
			{/if}
			{#if Number(timeoutDays) > MAX_WAIT_DAYS}
				<p class="text-destructive text-xs">
					Longer than {MAX_WAIT_DAYS} days — this will not publish.
				</p>
			{/if}
		{:else}
			<p class="text-muted-foreground/70 text-xs">
				Without a deadline the journey waits indefinitely. Fine for an event that always arrives; a
				trap for one that depends on a client remembering.
			</p>
		{/if}
	</div>

	{#if spec && refusal !== null}
		<Alert variant="destructive">
			<AlertDescription>
				<StatusBadge tone="danger">Can’t be waited for</StatusBadge> Nothing can release a journey parked
				here — {refusal}. This will not publish; choose another event.
			</AlertDescription>
		</Alert>
	{/if}
</div>
