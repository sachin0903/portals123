<script lang="ts">
	// One automation: what it does in plain English, a dry run, and its run log.
	//
	// The listing answers "what rules exist"; this answers the two questions you have once you care
	// about ONE of them — "would this do what I think?" (the dry run) and "why didn't it?" (the run
	// log, which records every candidate the consumer considered, not just the ones that fired).
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { Alert, AlertDescription, AlertTitle } from '../ui/alert/index.js';
	import { Badge } from '../ui/badge/index.js';
	import { Button } from '../ui/button/index.js';
	import { Card, CardContent, CardHeader, CardTitle } from '../ui/card/index.js';
	import { ConfirmDialog } from '../ui/confirm-dialog/index.js';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import PageHeader from '../shell/page-header.svelte';
	import { withToast } from '../book/clients/form-enhance.js';
	import AutomationSheet from './automation-sheet.svelte';
	import DryRunPanel from './dry-run-panel.svelte';
	import RunLog from './run-log.svelte';
	import {
		summariseConditions,
		summariseRearm,
		summariseSchedule,
		summariseTarget
	} from './summary.js';
	import type {
		AutomationActions,
		AutomationLinks,
		AutomationRow,
		AutomationSettings,
		DeliveryRow,
		DryRunResult,
		FlowTarget
	} from './types.js';

	let {
		automation,
		deliveries = [],
		hasMore = false,
		settings = null,
		flows = [],
		links,
		actions,
		dryRun = null,
		/** The host page's dry-run action name (gated on the write grant, like the sheet). */
		testActionName,
		onmore
	}: {
		automation: AutomationRow;
		deliveries?: DeliveryRow[];
		hasMore?: boolean;
		settings?: AutomationSettings | null;
		flows?: FlowTarget[];
		links: AutomationLinks;
		actions?: AutomationActions;
		dryRun?: DryRunResult | null;
		testActionName?: string;
		onmore?: () => void;
	} = $props();

	let sheetOpen = $state(false);
	let confirmDelete = $state(false);
	let deleteForm = $state<HTMLFormElement | null>(null);

	const canWrite = $derived(Boolean(actions?.update));
	const paused = $derived(settings?.paused ?? false);
	const defaultRearm = $derived(Number(settings?.effective?.default_rearm_minutes ?? 1440));
</script>

{#snippet headerActions()}
	{#if canWrite}
		<Button variant="outline" size="sm" onclick={() => (sheetOpen = true)}>
			<PencilIcon class="size-4" /> Edit
		</Button>
	{/if}
	{#if actions?.remove}
		<Button variant="ghost" size="sm" onclick={() => (confirmDelete = true)}>
			<Trash2Icon class="size-4" /> Delete
		</Button>
	{/if}
{/snippet}

<PageHeader
	eyebrow="Automation"
	title={automation.name}
	back={{ href: links.index, label: 'Automations' }}
	actions={canWrite || actions?.remove ? headerActions : undefined}
/>

<div class="space-y-4">
	{#if paused}
		<Alert variant="destructive">
			<AlertTitle>Automations are paused for this firm</AlertTitle>
			<AlertDescription>This won’t run until automations are resumed.</AlertDescription>
		</Alert>
	{:else if !automation.enabled}
		<Alert>
			<AlertTitle>This automation is switched off</AlertTitle>
			<AlertDescription>It won’t run until it is armed.</AlertDescription>
		</Alert>
	{:else if !automation.supported}
		<Alert>
			<AlertTitle>Nothing emits this trigger yet</AlertTitle>
			<AlertDescription>
				The rule is stored and will start working when that signal lands.
			</AlertDescription>
		</Alert>
	{/if}

	<Card>
		<CardHeader><CardTitle>What this does</CardTitle></CardHeader>
		<CardContent class="space-y-3 text-sm">
			<!-- The rule as three sentences, in the order it is authored and evaluated. -->
			<div class="grid gap-1 sm:grid-cols-[6rem_1fr]">
				<span class="text-muted-foreground">When</span>
				<span>
					{automation.trigger_label ?? automation.trigger_key}
					{#if automation.schedule}
						<span class="text-muted-foreground">
							— {summariseSchedule(automation.schedule) || automation.schedule.cron}
						</span>
					{/if}
				</span>
				<span class="text-muted-foreground">Only if</span>
				<span>{summariseConditions(automation.conditions)}</span>
				<span class="text-muted-foreground">Then</span>
				<span>{summariseTarget(automation)}</span>
			</div>
			<p class="text-muted-foreground border-t pt-3 text-xs">
				{summariseRearm(automation.rearm_minutes, defaultRearm)}
				{#if automation.allow_automation_source}
					Other automations may trigger it.
				{:else}
					Events caused by other automations don’t trigger it.
				{/if}
			</p>
			<div class="flex flex-wrap gap-1">
				<StatusBadge tone={automation.enabled ? 'success' : 'neutral'}>
					{automation.enabled ? 'Armed' : 'Off'}
				</StatusBadge>
				<Badge variant="outline">Precedence {automation.priority}</Badge>
			</div>
		</CardContent>
	</Card>

	<Card>
		<CardHeader><CardTitle>Try it</CardTitle></CardHeader>
		<CardContent>
			<DryRunPanel
				subjectKind={automation.subject_kind ?? 'contact'}
				result={dryRun}
				actionName={testActionName}
				enhance={actions?.enhance}
			/>
		</CardContent>
	</Card>

	<Card>
		<CardHeader><CardTitle>Run log</CardTitle></CardHeader>
		<CardContent>
			<RunLog {deliveries} {hasMore} {onmore} />
		</CardContent>
	</Card>
</div>

{#if actions?.remove}
	<form
		bind:this={deleteForm}
		method="POST"
		action={`?/${actions.remove}`}
		class="hidden"
		use:actions.enhance={withToast('Automation deleted.')}
	>
		<input type="hidden" name="uid" value={automation.uid} />
	</form>
	<ConfirmDialog
		bind:open={confirmDelete}
		title="Delete this automation?"
		description="It stops immediately and its run log goes with it. Switching it off instead keeps the history."
		confirmLabel="Delete"
		destructive
		onconfirm={() => deleteForm?.requestSubmit()}
	/>
{/if}

<AutomationSheet
	bind:open={sheetOpen}
	{automation}
	{flows}
	{actions}
	defaultRearmMinutes={defaultRearm}
/>
