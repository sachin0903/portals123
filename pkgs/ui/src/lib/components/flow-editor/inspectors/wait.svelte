<!--
	The wait inspector — pause for a length of time, or until a date the case already carries.

	The two modes are a Segmented toggle rather than "fill in whichever set of boxes you meant",
	because they are mutually exclusive to the engine (`until_date_field` wins) and a screen that
	lets an author fill in both is a screen that hides which one runs. Switching mode clears the
	other mode's keys for the same reason `setOperator` drops a value the new operator cannot read:
	a key that no longer renders must not keep a stale value that is silently still authored.
-->
<script lang="ts">
	import { Alert, AlertDescription, Input, Label, Segmented } from '@graphene/ui';
	import { FIELD_SCOPES } from '@graphene/ui/automations';
	import { FLOW_CAPS } from '@graphene/ui/book/journeys';
	import type { InspectorProps } from './types';
	import NextTargets from './next-targets.svelte';
	import { cfgString, describeWait, setCfgNumber, setCfgString } from './shared';

	let { node, nodes, canManage, onchange, onrestructure }: InspectorProps = $props();

	const MAX_WAIT_DAYS = FLOW_CAPS.maxWaitDays ?? 90;

	const MODES = [
		{ value: 'duration', label: 'For a while' },
		{ value: 'until', label: 'Until a date' }
	];

	// A duration wins at runtime (`timers.wait_until`), so a document carrying both is in duration
	// mode — the toggle must show what the engine would DO, not which field was authored last.
	const hasDuration = $derived(
		cfgString(node, 'duration_days') !== '' || cfgString(node, 'duration_hours') !== ''
	);
	const mode = $derived(!hasDuration && cfgString(node, 'until_date_field') ? 'until' : 'duration');
	const sentence = $derived(describeWait(node.config ?? {}));

	// The date fields a wait can key off: the closed registry's date-ish entries plus the open
	// `memory.*` namespace, which is where a flow puts a date it computed earlier. Offered as a
	// datalist rather than a hard dropdown for the same reason ConditionBuilder does — an authored
	// memory key must not be unreachable in its own picker.
	const dateFieldSuggestions = [
		...(FIELD_SCOPES.case ?? []).filter((f) => f.endsWith('_at')),
		'memory.review_due',
		'memory.deadline'
	];

	// Over the cap the engine refuses to publish; say so here rather than at publish, and say what
	// to do instead (the refusal's own words).
	const days = $derived(Number(node.config?.duration_days ?? 0) || 0);
	const hours = $derived(Number(node.config?.duration_hours ?? 0) || 0);
	const overCap = $derived(days + hours / 24 > MAX_WAIT_DAYS);

	function setMode(next: string): void {
		if (next === 'until') {
			delete node.config.duration_days;
			delete node.config.duration_hours;
			node.config.until_date_field = 'memory.review_due';
		} else {
			delete node.config.until_date_field;
			node.config.duration_days = 7;
		}
		onchange();
	}
</script>

<div class="space-y-4">
	<div class="space-y-1.5">
		<Label for="wait-title">What this wait is for</Label>
		<Input
			id="wait-title"
			value={node.title}
			disabled={!canManage}
			placeholder="e.g. Cooling-off period"
			oninput={(e) => {
				node.title = e.currentTarget.value;
				onchange();
			}}
		/>
	</div>

	<Segmented options={MODES} value={mode} disabled={!canManage} onValueChange={setMode} />

	{#if mode === 'until'}
		<div class="space-y-1.5">
			<Label for="wait-field">Wait until the date in</Label>
			<Input
				id="wait-field"
				list="wait-date-fields"
				value={cfgString(node, 'until_date_field')}
				disabled={!canManage}
				placeholder="memory.review_due"
				oninput={(e) => {
					setCfgString(node, 'until_date_field', e.currentTarget.value);
					onchange();
				}}
			/>
			<datalist id="wait-date-fields">
				{#each dateFieldSuggestions as field (field)}
					<option value={field}></option>
				{/each}
			</datalist>
			<p class="text-muted-foreground text-xs">
				A date the case already carries. If it is empty or already past when the journey reaches
				this node, the wait ends straight away.
			</p>
		</div>
	{:else}
		<div class="grid grid-cols-2 gap-3">
			<div class="space-y-1.5">
				<Label for="wait-days">Days</Label>
				<Input
					id="wait-days"
					type="number"
					min="0"
					max={String(MAX_WAIT_DAYS)}
					value={cfgString(node, 'duration_days')}
					disabled={!canManage}
					placeholder="0"
					oninput={(e) => {
						setCfgNumber(node, 'duration_days', e.currentTarget.value);
						onchange();
					}}
				/>
			</div>
			<div class="space-y-1.5">
				<Label for="wait-hours">Hours</Label>
				<Input
					id="wait-hours"
					type="number"
					min="0"
					value={cfgString(node, 'duration_hours')}
					disabled={!canManage}
					placeholder="0"
					oninput={(e) => {
						setCfgNumber(node, 'duration_hours', e.currentTarget.value);
						onchange();
					}}
				/>
			</div>
		</div>
	{/if}

	<p class="text-muted-foreground text-sm">{sentence}</p>

	{#if overCap}
		<Alert variant="warning">
			<AlertDescription>
				Longer than {MAX_WAIT_DAYS} days — this will not publish. A wait holds the case open the whole
				time; for an annual review use a scheduled automation instead.
			</AlertDescription>
		</Alert>
	{/if}

	<NextTargets {node} {nodes} {canManage} {onrestructure} />
</div>
