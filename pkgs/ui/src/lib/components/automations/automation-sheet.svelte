<script lang="ts">
	// The create/edit sheet: trigger → conditions → target.
	//
	// Three sections in the order an author thinks in — WHEN, IF, THEN — with the trigger picker and
	// the action config both generated from the contract, so the sheet knows nothing about any
	// particular trigger or action. The condition tree is edited as a model and serialised into one
	// hidden input on submit; the whole thing posts to the HOST page's form action with the app's
	// own `enhance` (pkgs/ui cannot import `$app/forms`).
	//
	// Serialisation happens in the submit handler, synchronously, and NOT via a `$state`-backed
	// hidden input: that pattern loses the last edit, because enhance builds the body from the DOM
	// before Svelte has flushed (the `attachStepAnswers` regression in book/journeys).
	import { Alert, AlertDescription } from '../ui/alert/index.js';
	import { Button } from '../ui/button/index.js';
	import { Input } from '../ui/input/index.js';
	import { Label } from '../ui/label/index.js';
	import { SelectMenu } from '../ui/select-menu/index.js';
	import { Switch } from '../ui/switch/index.js';
	import * as Sheet from '../ui/sheet/index.js';
	import ActionConfigFields from './action-config.svelte';
	import { withToast } from '../book/clients/form-enhance.js';
	import {
		OFFERED_ACTION_TYPES,
		configFields,
		triggerGroups,
		TRIGGER_BY_KEY,
		isSupported
	} from './contract.js';
	import ConditionBuilder from './condition-builder.svelte';
	import { toJson, toModel, type BuilderGroup } from './conditions.js';
	import { summariseSchedule } from './summary.js';
	import type { AutomationActions, AutomationRow, FlowTarget } from './types.js';

	let {
		open = $bindable(false),
		/** The row being edited; `null` creates. */
		automation = null,
		flows = [],
		actions,
		/** The tenant's default re-arm window, shown as the placeholder. */
		defaultRearmMinutes = 1440
	}: {
		open?: boolean;
		automation?: AutomationRow | null;
		flows?: FlowTarget[];
		actions?: AutomationActions;
		defaultRearmMinutes?: number;
	} = $props();

	const editing = $derived(automation !== null);
	const actionName = $derived(editing ? actions?.update : actions?.create);

	// ── Draft state ─────────────────────────────────────────────────────────
	// Re-seeded whenever the sheet opens on a different row. Keyed on the uid (plus `open`) rather
	// than a deep compare: an edit in flight must never be clobbered by the parent re-rendering the
	// same row, which is exactly what a value-watching effect would do.
	let seeded = $state<string | null>(null);
	let name = $state('');
	let triggerKey = $state('contact.created');
	let targetKind = $state<'flow' | 'action'>('action');
	let flowUid = $state('');
	let actionType = $state('apply_tag');
	let actionConfig = $state<Record<string, string>>({});
	let conditions = $state<BuilderGroup>(toModel(null));
	let cronExpression = $state('0 9 * * 1-5');
	let rearm = $state('');
	let allowAutomationSource = $state(false);
	let enabled = $state(true);
	let pending = $state(false);

	$effect(() => {
		const key = `${open ? 'o' : 'c'}:${automation?.uid ?? 'new'}`;
		if (key === seeded) return;
		seeded = key;
		if (!open) return;
		name = automation?.name ?? '';
		triggerKey = automation?.trigger_key ?? 'contact.created';
		targetKind = automation?.flow_uid ? 'flow' : 'action';
		flowUid = automation?.flow_uid ?? flows[0]?.uid ?? '';
		actionType = automation?.action?.type ?? 'apply_tag';
		actionConfig = Object.fromEntries(
			Object.entries(automation?.action?.config ?? {}).map(([k, v]) => [
				k,
				v === null || v === undefined ? '' : String(v)
			])
		);
		conditions = toModel(automation?.conditions ?? null);
		cronExpression = automation?.schedule?.cron ?? '0 9 * * 1-5';
		rearm =
			automation?.rearm_minutes === null || automation?.rearm_minutes === undefined
				? ''
				: String(automation.rearm_minutes);
		allowAutomationSource = automation?.allow_automation_source ?? false;
		enabled = automation?.enabled ?? true;
	});

	const spec = $derived(TRIGGER_BY_KEY[triggerKey]);
	const isScheduled = $derived(spec?.source === 'schedule');
	const fields = $derived(configFields(actionType));

	const triggerItems = triggerGroups().map((group) => ({
		label: group.label,
		options: group.triggers.map((t) => ({
			value: t.key,
			// The menu says which entries are not live yet rather than hiding them: a firm that
			// authors one should know it is parked, and hiding half the contract makes the picker
			// look broken next to the docs.
			label: isSupported(t.key) ? t.label : `${t.label} (not live yet)`
		}))
	}));

	const flowItems = $derived(flows.map((f) => ({ value: f.uid, label: f.name })));
	const actionItems = OFFERED_ACTION_TYPES.map((a) => ({ value: a.type, label: a.label }));

	function submit() {
		return withToast(editing ? 'Automation saved.' : 'Automation armed.', {
			pending: (v) => (pending = v),
			onSuccess: () => (open = false),
			reset: false
		});
	}

	/** Build the body synchronously from the live model — never from a rendered hidden input. */
	function attach(body: FormData) {
		if (automation) body.set('uid', automation.uid);
		body.set('name', name.trim());
		body.set('trigger_key', triggerKey);
		const json = toJson(conditions);
		body.set('conditions', json === null ? '' : JSON.stringify(json));
		body.set('target', targetKind);
		body.set('flow_uid', targetKind === 'flow' ? flowUid : '');
		body.set('action_type', targetKind === 'action' ? actionType : '');
		body.set(
			'action_config',
			targetKind === 'action'
				? JSON.stringify(
						Object.fromEntries(
							fields
								.map((f) => [f.key, actionConfig[f.key] ?? ''] as const)
								.filter(([, v]) => v !== '')
						)
					)
				: ''
		);
		body.set('schedule_cron', isScheduled ? cronExpression.trim() : '');
		body.set('rearm_minutes', rearm.trim());
		body.set('allow_automation_source', allowAutomationSource ? '1' : '');
		body.set('enabled', enabled ? '1' : '');
	}
</script>

<Sheet.Root bind:open>
	<Sheet.Content side="right" class="flex w-full flex-col gap-0 p-0">
		<Sheet.Header class="border-b px-6 py-4">
			<Sheet.Title>{editing ? 'Edit automation' : 'New automation'}</Sheet.Title>
			<Sheet.Description>
				When something happens and your conditions hold, start a journey or perform one action.
			</Sheet.Description>
		</Sheet.Header>

		{#if !actionName || !actions}
			<div class="p-6">
				<Alert>
					<AlertDescription>
						You don’t have permission to change automations. Ask someone with the automations
						permission to make this change.
					</AlertDescription>
				</Alert>
			</div>
		{:else}
			<form
				method="POST"
				action={`?/${actionName}`}
				use:actions.enhance={(input) => {
					attach(input.formData);
					return submit()(input);
				}}
				class="flex min-h-0 flex-1 flex-col"
			>
				<div class="min-h-0 flex-1 space-y-6 overflow-y-auto px-6 py-5">
					<div class="space-y-2">
						<Label for="automation-name">Name</Label>
						<Input
							id="automation-name"
							bind:value={name}
							required
							maxlength={120}
							placeholder="Chase an unsigned fact find"
						/>
						<p class="text-muted-foreground text-xs">
							How this rule reads in the list and in the audit trail.
						</p>
					</div>

					<!-- WHEN -->
					<section class="space-y-2">
						<Label for="automation-trigger">When</Label>
						<SelectMenu
							id="automation-trigger"
							items={triggerItems}
							bind:value={triggerKey}
							placeholder="Choose a trigger"
						/>
						{#if spec && !isSupported(spec.key)}
							<Alert>
								<AlertDescription>
									Nothing emits this trigger yet, so this automation will be stored but won’t fire.
									It will start working when that signal lands.
								</AlertDescription>
							</Alert>
						{/if}
						{#if isScheduled}
							<div class="space-y-1 pt-2">
								<Label for="automation-cron">Schedule</Label>
								<Input
									id="automation-cron"
									bind:value={cronExpression}
									placeholder="0 9 * * 1-5"
									aria-describedby="automation-cron-hint"
								/>
								<p id="automation-cron-hint" class="text-muted-foreground text-xs">
									Minute, hour, day, month, weekday — in the firm’s timezone.
									{#if summariseSchedule({ cron: cronExpression })}
										Runs {summariseSchedule({ cron: cronExpression })}.
									{/if}
								</p>
							</div>
						{/if}
					</section>

					<!-- IF -->
					<section class="space-y-2">
						<Label>Only if</Label>
						<ConditionBuilder model={conditions} onchange={(next) => (conditions = next)} />
					</section>

					<!-- THEN -->
					<section class="space-y-3">
						<Label>Then</Label>
						<div class="flex gap-2">
							<Button
								type="button"
								variant={targetKind === 'flow' ? 'default' : 'outline'}
								size="sm"
								onclick={() => (targetKind = 'flow')}
							>
								Start a journey
							</Button>
							<Button
								type="button"
								variant={targetKind === 'action' ? 'default' : 'outline'}
								size="sm"
								onclick={() => (targetKind = 'action')}
							>
								Perform an action
							</Button>
						</div>

						{#if targetKind === 'flow'}
							{#if flows.length === 0}
								<Alert>
									<AlertDescription>
										There are no published journeys to start yet. Publish one first.
									</AlertDescription>
								</Alert>
							{:else}
								<SelectMenu items={flowItems} bind:value={flowUid} placeholder="Choose a journey" />
							{/if}
						{:else}
							<SelectMenu
								items={actionItems}
								bind:value={actionType}
								placeholder="Choose an action"
							/>
							<ActionConfigFields
								type={actionType}
								values={actionConfig}
								onchange={(key, value) => (actionConfig = { ...actionConfig, [key]: value })}
							/>
						{/if}
					</section>

					<!-- Safety -->
					<section class="space-y-3 border-t pt-5">
						<div class="space-y-1">
							<Label for="automation-rearm">Don’t re-fire within (minutes)</Label>
							<Input
								id="automation-rearm"
								type="number"
								min="0"
								bind:value={rearm}
								placeholder={String(defaultRearmMinutes)}
							/>
							<p class="text-muted-foreground text-xs">
								Per subject. Blank uses the firm’s default.
							</p>
						</div>
						<div class="space-y-1">
							<div class="flex items-center justify-between gap-4">
								<Label for="automation-self">Let other automations trigger this one</Label>
								<Switch id="automation-self" bind:checked={allowAutomationSource} />
							</div>
							<p class="text-muted-foreground text-xs">
								Off by default. Automations that feed each other are the usual way a rule storm
								starts.
							</p>
						</div>
						<div class="flex items-center justify-between gap-4">
							<Label for="automation-enabled">Armed</Label>
							<Switch id="automation-enabled" bind:checked={enabled} />
						</div>
					</section>
				</div>

				<Sheet.Footer class="border-t px-6 py-4">
					<Button type="button" variant="ghost" onclick={() => (open = false)}>Cancel</Button>
					<Button type="submit" disabled={pending || !name.trim()}>
						{pending ? 'Saving…' : editing ? 'Save' : 'Arm automation'}
					</Button>
				</Sheet.Footer>
			</form>
		{/if}
	</Sheet.Content>
</Sheet.Root>
