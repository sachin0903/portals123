<script lang="ts">
	// The config form for ONE action — the same fields wherever an action is authored.
	//
	// There are two places an action gets configured: a rule in the automations sheet, and an
	// `action` node in the flow builder. They are the same vocabulary run by the same executor, so
	// they must be the same form; two hand-built copies would drift the day a config key is added
	// to the contract and only one of them grows a field for it.
	//
	// Descriptor-driven, like `ConditionBuilder`: everything comes from `configFields(type)`, which
	// derives from flow-contract.json. This component knows nothing about any particular action.
	//
	// Values are held as STRINGS (what an input holds) and handed back as strings; serialising them
	// — dropping the blanks, coercing the numbers — is the caller's business, because the rule tier
	// posts a form body and the flow builder writes a node's `config` object.
	import { Input } from '../ui/input/index.js';
	import { Label } from '../ui/label/index.js';
	import { SelectMenu } from '../ui/select-menu/index.js';
	import { Textarea } from '../ui/textarea/index.js';
	import { configFields } from './contract.js';

	let {
		type,
		values,
		onchange,
		disabled = false,
		/** Prefix for the field ids — two of these on one screen must not share an id. */
		idPrefix = 'cfg',
		/**
		 * Real choices for a config key the descriptor can only describe as text — a template uid, a
		 * role slug, a document. The DESCRIPTOR stays the single source of which fields exist and
		 * what they are called; this only upgrades how one of them is picked, where the host happens
		 * to have loaded the list. A key with no entry here renders exactly as it always did.
		 */
		options = {}
	}: {
		type: string;
		values: Record<string, string>;
		onchange: (key: string, value: string) => void;
		disabled?: boolean;
		idPrefix?: string;
		options?: Record<string, { value: string; label: string }[]>;
	} = $props();

	const fields = $derived(
		configFields(type).map((field) =>
			options[field.key]?.length
				? { ...field, kind: 'choice' as const, choices: options[field.key] }
				: field
		)
	);
</script>

<div class="space-y-3">
	{#each fields as field (field.key)}
		{@const id = `${idPrefix}-${field.key}`}
		<div class="space-y-1">
			<Label for={id}>{field.label}</Label>
			{#if field.kind === 'textarea'}
				<!-- `placeholder` reaches the textarea too, not the text input alone: it is where a
				     map-shaped key shows its shape, and it used to render as a blank box. -->
				<Textarea
					{id}
					{disabled}
					placeholder={field.placeholder}
					value={values[field.key] ?? ''}
					oninput={(e) => onchange(field.key, e.currentTarget.value)}
				/>
			{:else if field.kind === 'choice' && field.choices}
				<SelectMenu
					{id}
					{disabled}
					items={field.choices}
					value={values[field.key] || field.defaultValue || ''}
					placeholder="Choose…"
					onValueChange={(v) => onchange(field.key, v)}
				/>
			{:else}
				<Input
					{id}
					{disabled}
					type={field.kind === 'number' ? 'number' : 'text'}
					placeholder={field.placeholder}
					value={values[field.key] ?? ''}
					oninput={(e) => onchange(field.key, e.currentTarget.value)}
				/>
			{/if}
		</div>
	{/each}
</div>
