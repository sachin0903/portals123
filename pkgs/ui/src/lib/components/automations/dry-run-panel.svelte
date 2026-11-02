<script lang="ts">
	// The dry run — pick a subject, see what this automation WOULD do. Nothing is performed.
	//
	// This is the feature that makes a rule authorable with confidence, and its whole value rests on
	// being trustworthy: the panel therefore states plainly that nothing ran, shows each condition's
	// own verdict beside the value it actually read, and separates "the conditions matched" from
	// "it would fire" — a matching rule that is disarmed, or whose firm is paused, still does
	// nothing, and those are different fixes.
	//
	// The verdict comes from the SERVER (`POST /automations/{uid}/test`), evaluated by the same
	// evaluator the engine uses. A browser-side re-implementation would be a second semantics, which
	// is exactly what the shared contract exists to prevent.
	import FlaskConicalIcon from '@lucide/svelte/icons/flask-conical';
	import ExplainTree from './explain-tree.svelte';
	import { Alert, AlertDescription, AlertTitle } from '../ui/alert/index.js';
	import { Button } from '../ui/button/index.js';
	import { Input } from '../ui/input/index.js';
	import { Label } from '../ui/label/index.js';
	import type { AutomationActions, DryRunResult } from './types.js';

	let {
		subjectKind = 'contact',
		result = null,
		actionName,
		enhance,
		pending = false
	}: {
		subjectKind?: string;
		/** The last dry run's outcome, from the page's form action. */
		result?: DryRunResult | null;
		/** The host page's test action name. Absent ⇒ the panel explains why it isn't offered. */
		actionName?: string;
		enhance?: AutomationActions['enhance'];
		pending?: boolean;
	} = $props();
</script>

<div class="space-y-4">
	{#if actionName && enhance}
		<form
			method="POST"
			action={`?/${actionName}`}
			use:enhance
			class="flex flex-wrap items-end gap-2"
		>
			<div class="space-y-1">
				<Label for="dry-run-subject">Test against a {subjectKind}</Label>
				<Input
					id="dry-run-subject"
					name="subject_id"
					type="number"
					min="1"
					placeholder="Record id"
					class="w-40"
				/>
			</div>
			<Button type="submit" variant="outline" disabled={pending}>
				<FlaskConicalIcon class="size-4" />
				{pending ? 'Testing…' : 'Dry run'}
			</Button>
			<p class="text-muted-foreground w-full text-xs">
				A dry run never performs the action, never starts a journey and never sends anything. It
				only reports what would happen.
			</p>
		</form>
	{:else}
		<p class="text-muted-foreground text-sm">
			You don’t have permission to dry-run this automation.
		</p>
	{/if}

	{#if result}
		<div class="space-y-3">
			<Alert variant={result.would_fire ? 'default' : undefined}>
				<AlertTitle>
					{result.would_fire ? 'This would fire' : 'This would not fire'}
				</AlertTitle>
				<AlertDescription>
					<p>
						Nothing was performed — this was a dry run.
						{#if !result.subject.found && result.subject.id !== null}
							No {result.subject.kind} with that id was found in this firm.
						{/if}
					</p>
					{#if result.blockers.length}
						<ul class="mt-2 list-disc pl-5 text-sm">
							{#each result.blockers as blocker (blocker)}
								<li>{blocker}</li>
							{/each}
						</ul>
					{/if}
				</AlertDescription>
			</Alert>

			<div class="space-y-1">
				<p class="text-muted-foreground text-xs font-medium">Conditions</p>
				<ExplainTree node={result.explanation} />
			</div>
		</div>
	{/if}
</div>
