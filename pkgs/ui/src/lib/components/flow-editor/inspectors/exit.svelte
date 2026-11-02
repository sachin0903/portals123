<!--
	The end inspector.

	Small, but the outcome is not cosmetic: "completed" is what `flows.case.completed` fires on, so
	a chained journey listening for it starts (or does not) depending on which of these three words
	an author picked. Hence a hint per outcome rather than a bare dropdown, and hence the default is
	stated rather than left implicit — an `exit` with no authored outcome completes.
-->
<script lang="ts">
	import { Input, Label, SelectMenu, Textarea } from '@graphene/ui';
	import type { InspectorProps } from './types';
	import { cfgString, EXIT_OUTCOMES, setCfgString } from './shared';

	let { node, canManage, onchange }: InspectorProps = $props();

	const outcome = $derived(cfgString(node, 'outcome') || 'completed');
	const items = EXIT_OUTCOMES.map((o) => ({ value: o.value, label: o.label }));
	const hint = $derived(EXIT_OUTCOMES.find((o) => o.value === outcome)?.hint ?? '');
</script>

<div class="space-y-4">
	<div class="space-y-1.5">
		<Label for="exit-title">What this ending is</Label>
		<Input
			id="exit-title"
			value={node.title}
			disabled={!canManage}
			placeholder="e.g. Onboarding complete"
			oninput={(e) => {
				node.title = e.currentTarget.value;
				onchange();
			}}
		/>
	</div>

	<div class="space-y-1.5">
		<Label for="exit-outcome">The journey ends as</Label>
		<SelectMenu
			id="exit-outcome"
			size="sm"
			{items}
			value={outcome}
			disabled={!canManage}
			onValueChange={(v) => {
				setCfgString(node, 'outcome', v);
				onchange();
			}}
		/>
		<p class="text-muted-foreground text-xs">{hint}</p>
	</div>

	<div class="space-y-1.5">
		<Label for="exit-reason">Reason</Label>
		<Textarea
			id="exit-reason"
			value={cfgString(node, 'reason')}
			disabled={!canManage}
			placeholder="Why a case that reaches here ended this way"
			oninput={(e) => {
				setCfgString(node, 'reason', e.currentTarget.value);
				onchange();
			}}
		/>
		<p class="text-muted-foreground text-xs">
			Recorded on the case and shown to staff reading it later. Optional, and worth writing for
			anything other than the happy ending.
		</p>
	</div>
</div>
