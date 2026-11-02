<script lang="ts">
	// One screening match as the AML console decides it: who ComplyCube matched (`AmlMatchProfile`,
	// the profile a firm's compliance tab shows too), and under it Graphene's decision: a true match
	// or a false positive, with an optional comment. The form posts the page's `decideMatch` action
	// (the client page and the workbench both have it).
	import { enhance } from '$app/forms';
	import { AmlMatchProfile, Button, Card, CardContent, Label, Textarea } from '@graphene/ui';
	import {
		MATCH_ACTIONS,
		dateTime,
		matchStatus,
		type AmlMatch,
		type AmlMatchDecision
	} from '$lib/aml';
	import DecisionBox from './decision-box.svelte';
	import { submit } from './submit';

	let {
		match,
		checkUid,
		canDecide,
		lastOpen = false,
		portal = true,
		compact = false,
		ondecided
	}: {
		match: AmlMatch;
		/** The AML check the match is on: the form names it. */
		checkUid: string;
		/** The operator may decide, and the check is still open to decisions. */
		canDecide: boolean;
		/** This is the only undecided match left on the check. */
		lastOpen?: boolean;
		/** A portal client's screen: a confirmed match holds their journey. */
		portal?: boolean;
		/** Only the top of the profile, its details folded: the workbench works many in a row. */
		compact?: boolean;
		/** A decision was recorded; the page reloads straight after. */
		ondecided?: () => void;
	} = $props();

	const status = $derived(matchStatus(match));

	// A decided match can be changed until its check settles: an operator who got one wrong needs the
	// route back. The choice comes back on request, so it is not the obvious click.
	let changing = $state(false);
	const choosing = $derived(canDecide && (match.decision === null || changing));
	let comment = $state('');
	let deciding = $state(false);

	/** Each decision's look: a confirmed match holds or refers the client, so the two never read alike. */
	const ACTION_CLASS: Record<AmlMatchDecision, string> = {
		confirmed: 'border-destructive-line text-destructive-strong hover:bg-destructive-subtle',
		dismissed: 'border-success-line text-success-strong hover:bg-success-subtle'
	};

	const hint = $derived(
		[
			lastOpen ? 'This is the last open match.' : null,
			portal
				? "A true match holds the client's journey until Graphene allows it to continue."
				: 'A true match refers the client.',
			'Marking every match a false positive clears the screen.'
		]
			.filter(Boolean)
			.join(' ')
	);

	function done() {
		changing = false;
		comment = '';
		ondecided?.();
	}
</script>

<Card glass size="sm" class="gap-3">
	<CardContent>
		<AmlMatchProfile {match} nameReviewer foldDetails={compact}>
			<DecisionBox label={status.label} tone={status.tone}>
				{#if match.decision}
					<div class="text-xs">
						<span class="text-muted-foreground">
							{match.decided_by ?? 'Graphene'} · {dateTime(match.decided_at)}
						</span>
						{#if match.comment}<p class="mt-0.5 text-sm">{match.comment}</p>{/if}
					</div>
				{/if}
				{#if choosing}
					<form
						method="POST"
						action="?/decideMatch"
						use:enhance={submit('Decision recorded.', (b) => (deciding = b), done)}
						class="space-y-2"
					>
						<input type="hidden" name="check" value={checkUid} />
						<input type="hidden" name="match" value={match.uid} />
						<Label for={`match-comment-${match.uid}`} class="text-xs">Comment (optional)</Label>
						<Textarea
							id={`match-comment-${match.uid}`}
							name="comment"
							bind:value={comment}
							rows={2}
							placeholder="What was checked, and against what."
						/>
						<div class="flex flex-wrap items-center gap-2">
							{#each MATCH_ACTIONS as action (action.value)}
								<Button
									type="submit"
									name="decision"
									value={action.value}
									variant="outline"
									size="sm"
									class={ACTION_CLASS[action.value]}
									disabled={deciding}>{action.label}</Button
								>
							{/each}
							{#if changing}
								<Button type="button" variant="ghost" size="sm" onclick={() => (changing = false)}
									>Cancel</Button
								>
							{/if}
						</div>
						<p class="text-muted-foreground text-xs">{hint}</p>
					</form>
				{:else if canDecide && match.decision}
					<Button variant="ghost" size="sm" class="-ml-2" onclick={() => (changing = true)}
						>Change</Button
					>
				{/if}
			</DecisionBox>
		</AmlMatchProfile>
	</CardContent>
</Card>
