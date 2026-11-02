<!--
	The hand-over inspector.

	The one thing this screen exists to say out loud: **control does not come back**. It is a tail
	call, not a subroutine call — the contract's word — and an author who reads "Start another
	journey" without that will wire work after it and wonder why the work never happens. So the
	semantics are stated in the panel's own copy, not left to a tooltip, and there is no "then go
	to" picker at all (the engine refuses a `next` on this kind, and offering one would be an
	invitation to author a refusal).

	The flow is chosen from the list the page loads. When that list is empty the input degrades to
	free text on the code, because a firm may be authoring the two journeys in either order and
	blocking the field would make the second one unauthorable until the first exists.
-->
<script lang="ts">
	import { Alert, AlertDescription, Input, Label, SelectMenu, Switch } from '@graphene/ui';
	import type { InspectorProps } from './types';
	import { cfgString, setCfgString } from './shared';

	let { node, flows = [], canManage, onchange }: InspectorProps = $props();

	const code = $derived(cfgString(node, 'flow_code'));
	const carry = $derived(node.config?.carry_memory !== false);
	const items = $derived(flows.map((f) => ({ value: f.code, label: f.name })));
	const chosen = $derived(flows.find((f) => f.code === code));
</script>

<div class="space-y-4">
	<div class="space-y-1.5">
		<Label for="handoff-title">What this hand-over is</Label>
		<Input
			id="handoff-title"
			value={node.title}
			disabled={!canManage}
			placeholder="e.g. Hand over to annual review"
			oninput={(e) => {
				node.title = e.currentTarget.value;
				onchange();
			}}
		/>
	</div>

	<div class="space-y-1.5">
		<Label for="handoff-flow">Start</Label>
		{#if flows.length}
			<SelectMenu
				id="handoff-flow"
				size="sm"
				{items}
				value={code}
				placeholder="Choose a journey…"
				disabled={!canManage}
				onValueChange={(v) => {
					setCfgString(node, 'flow_code', v);
					onchange();
				}}
			/>
		{:else}
			<Input
				id="handoff-flow"
				value={code}
				disabled={!canManage}
				placeholder="the other journey's code, e.g. annual-review"
				oninput={(e) => {
					setCfgString(node, 'flow_code', e.currentTarget.value);
					onchange();
				}}
			/>
			<p class="text-muted-foreground text-xs">
				No other journeys to pick from yet — type the code of the one you are about to build.
			</p>
		{/if}
		{#if code && flows.length && !chosen}
			<p class="text-destructive text-xs">
				No journey in this firm has the code “{code}”.
			</p>
		{/if}
	</div>

	<div class="space-y-1">
		<div class="flex items-center justify-between gap-4">
			<Label for="handoff-memory" class="font-normal">Carry what this journey learned</Label>
			<Switch
				id="handoff-memory"
				checked={carry}
				disabled={!canManage}
				onCheckedChange={(v) => {
					node.config.carry_memory = v === true;
					onchange();
				}}
			/>
		</div>
		<p class="text-muted-foreground text-xs">
			Passes this case's memory to the new one, so it starts knowing the answers already collected.
			Off means the new journey starts blank.
		</p>
	</div>

	<Alert>
		<AlertDescription>
			<strong>This journey ends here.</strong>
			{chosen?.name ?? 'The other journey'} takes over and control never comes back, so nothing after
			this node would run. To carry on afterwards, have the other journey do it — or listen for “a journey
			completes”.
		</AlertDescription>
	</Alert>
</div>
