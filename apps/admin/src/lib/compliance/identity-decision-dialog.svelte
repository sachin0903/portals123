<script lang="ts" module>
	export type CheckDecision = 'clear' | 'rejected';
</script>

<script lang="ts">
	// Graphene's decision on an identity check: clear or reject it, with a reason and a note. The form
	// posts the page's `decideCheck` action. Mounted per open, so each open starts empty.
	import { enhance } from '$app/forms';
	import { Button, Dialog, Label, SelectMenu, Textarea } from '@graphene/ui';
	import type { AmlCheckDetail } from '$lib/aml';
	import { submit } from './submit';

	let {
		target,
		clientName,
		error = null,
		onclose
	}: {
		/** The check, and the decision chosen on its card. */
		target: { check: AmlCheckDetail; decision: CheckDecision };
		clientName: string;
		/** The `decideCheck` action's refusal, shown above the buttons. */
		error?: string | null;
		/** Cancelled, dismissed, or the decision was recorded. */
		onclose: () => void;
	} = $props();

	const CHECK_REASONS: Record<CheckDecision, string[]> = {
		clear: [
			'Document and selfie verified on review',
			'Image quality issue only',
			'Verified through another document',
			'Other'
		],
		rejected: [
			'The images could not be checked, new ones are needed',
			'Document appears altered or fake',
			'Selfie does not match the document',
			'Document expired',
			'Other'
		]
	};

	let checkReason = $state('');
	let checkNote = $state('');
	let decidingCheck = $state(false);

	const checkReasonItems = $derived(
		CHECK_REASONS[target.decision].map((r) => ({ value: r, label: r }))
	);
</script>

<Dialog.Root open onOpenChange={(o) => !o && onclose()}>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>
				{target.decision === 'clear' ? 'Clear this identity check' : 'Reject this identity check'}
			</Dialog.Title>
			<Dialog.Description>
				{target.decision === 'clear'
					? `${clientName} passes identity.${target.check.check.origin === 'portal' ? ' Their onboarding moves on.' : ''}`
					: `${clientName} fails identity. The firm sees the check as failed.`}
			</Dialog.Description>
		</Dialog.Header>
		<form
			method="POST"
			action="?/decideCheck"
			use:enhance={submit(
				target.decision === 'clear' ? 'Check cleared.' : 'Check rejected.',
				(b) => (decidingCheck = b),
				() => onclose()
			)}
			class="space-y-4"
		>
			<input type="hidden" name="check" value={target.check.check.uid} />
			<input type="hidden" name="decision" value={target.decision} />
			<div class="space-y-2">
				<Label for="check-reason">Reason</Label>
				<SelectMenu
					id="check-reason"
					name="reason"
					items={checkReasonItems}
					bind:value={checkReason}
					placeholder="Choose a reason…"
					required
				/>
			</div>
			<div class="space-y-2">
				<Label for="check-note">
					Note {checkReason === 'Other' ? '(required)' : '(optional)'}
				</Label>
				<Textarea
					id="check-note"
					name="note"
					bind:value={checkNote}
					rows={3}
					placeholder="What was checked, and against what."
				/>
			</div>
			{#if error}
				<p class="text-destructive text-xs">{error}</p>
			{/if}
			<Dialog.Footer>
				<Button type="button" variant="outline" onclick={() => onclose()}>Cancel</Button>
				<Button
					type="submit"
					variant={target.decision === 'rejected' ? 'destructive' : 'default'}
					disabled={decidingCheck || !checkReason || (checkReason === 'Other' && !checkNote.trim())}
				>
					{decidingCheck
						? 'Saving…'
						: target.decision === 'clear'
							? 'Clear the check'
							: 'Reject the check'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
