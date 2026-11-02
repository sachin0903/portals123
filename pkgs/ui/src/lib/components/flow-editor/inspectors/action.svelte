<!--
	The action inspector — unattended work the engine performs, sitting inline in a journey.

	The config form is `ActionConfigFields` from `@graphene/ui/automations`, the SAME component the
	rule sheet uses, driven by the same `configFields(type)` descriptor derived from
	flow-contract.json. That is the point: a flow's action and a rule's action are one vocabulary run
	by one executor, so a config key added to the contract must appear in both or neither. A
	hand-built form here would drift the first time somebody added a key.

	Where this surface knows more than the rule sheet does — the flow page has loaded the firm's
	templates, documents and roles — it passes them as `options` for the keys that name one. The
	descriptor still decides WHICH fields exist; the host only improves how one is picked.

	The permission line is deliberate copy, not decoration. Every action asserts the authority a
	person would need to do the same thing by hand, publish refuses one whose firm has granted that
	authority to nobody, and the author should read that while choosing rather than in a refusal.
-->
<script lang="ts">
	import { Alert, AlertDescription, Input, Label, SelectMenu } from '@graphene/ui';
	import {
		ACTION_BY_TYPE,
		OFFERED_ACTION_TYPES,
		ActionConfigFields,
		actionPermissionNote,
		configFields,
		isActionAvailable
	} from '@graphene/ui/automations';
	import ShieldIcon from '@lucide/svelte/icons/shield';
	import type { InspectorProps } from './types';
	import NextTargets from './next-targets.svelte';

	let {
		node,
		nodes,
		documents,
		templates,
		roles = [],
		canManage,
		onchange,
		onrestructure
	}: InspectorProps = $props();

	const actionItems = OFFERED_ACTION_TYPES.map((a) => ({
		value: a.type,
		// Parked types stay visible and disabled — see PARKED_ACTIONS in the shared contract.
		label: isActionAvailable(a.type) ? a.label : `${a.label} (unavailable)`,
		disabled: !isActionAvailable(a.type)
	}));

	const type = $derived(node.type ?? '');
	const spec = $derived(ACTION_BY_TYPE[type]);
	const permissionNote = $derived(actionPermissionNote(type));

	/** The lists this page happens to hold, for the config keys that name one of them. */
	const options = $derived({
		template_uid: templates.map((t) => ({ value: t.uid, label: t.name })),
		document_uid: documents.map((d) => ({ value: d.uid, label: d.name })),
		assignee_role_slug: roles.map((r) => ({ value: r.slug, label: r.name }))
	});

	// The config as strings — what an input holds. Written back coerced, because the contract's
	// number fields (`due_in_days`) must not reach the engine as "7".
	const values = $derived(
		Object.fromEntries(
			Object.entries(node.config ?? {}).map(([k, v]) => [
				k,
				v === null || v === undefined ? '' : String(v)
			])
		) as Record<string, string>
	);

	function setConfig(key: string, value: string): void {
		const trimmed = value.trim();
		if (trimmed === '') {
			delete node.config[key];
		} else if (configFields(type).find((f) => f.key === key)?.kind === 'number') {
			const n = Number(trimmed);
			node.config[key] = Number.isFinite(n) ? n : trimmed;
		} else {
			node.config[key] = trimmed;
		}
		onchange();
	}

	function changeType(next: string): void {
		// `FlowNode.type` is typed as the STEP vocabulary because a node is a step plus two keys; on
		// an action node the same field carries an action type, which is exactly the ambiguity the
		// contract settled with `kind`. The cast is that discriminator, spelled out.
		node.type = next as NonNullable<typeof node.type>;
		// Config keys are allowlisted per action type — a leftover key from the old type is a save
		// that 422s with "unknown config keys", so the switch clears rather than merges.
		node.config = {};
		if (!node.title?.trim()) node.title = ACTION_BY_TYPE[next]?.label ?? next;
		onrestructure();
	}
</script>

<div class="space-y-4">
	<div class="space-y-1.5">
		<Label for="action-type">The engine will</Label>
		<SelectMenu
			id="action-type"
			size="sm"
			items={actionItems}
			value={type}
			placeholder="Choose an action…"
			disabled={!canManage}
			onValueChange={changeType}
		/>
		{#if type && !spec}
			<p class="text-destructive text-xs">
				“{type}” isn’t an action this engine can perform.
			</p>
		{/if}
	</div>

	{#if spec && !isActionAvailable(type)}
		<Alert variant="warning">
			<AlertDescription>
				This action isn’t built yet, so a journey containing it will not publish. Pick another, or
				raise a task for someone to do it by hand in the meantime.
			</AlertDescription>
		</Alert>
	{/if}

	<div class="space-y-1.5">
		<Label for="action-title">Called</Label>
		<Input
			id="action-title"
			value={node.title}
			disabled={!canManage}
			placeholder="How this reads on the canvas"
			oninput={(e) => {
				node.title = e.currentTarget.value;
				onchange();
			}}
		/>
	</div>

	{#if spec}
		<ActionConfigFields
			{type}
			{values}
			{options}
			idPrefix="action-cfg"
			disabled={!canManage}
			onchange={setConfig}
		/>

		{#if permissionNote}
			<p class="text-muted-foreground flex items-start gap-1.5 text-xs">
				<ShieldIcon class="mt-0.5 size-3.5 shrink-0" />
				<span>{permissionNote}</span>
			</p>
		{/if}
	{/if}

	<NextTargets {node} {nodes} {canManage} {onrestructure} />
	<div class="-mt-2">
		<p class="text-muted-foreground text-xs">
			Nobody is assigned this — it runs the moment the journey reaches it, and the journey carries
			straight on.
		</p>
	</div>
</div>
