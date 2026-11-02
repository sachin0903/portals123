<script lang="ts">
	// Reject · Approve on a held run, with the words that go with each. ONE component, so the run
	// panel, the run detail and a gate node in the tree cannot drift: a rejection always asks
	// why (the API refuses one without a reason, and the ops card used to post none and fail
	// every time), and approving says what it does for THIS kind of hold — a book change writes
	// the record; a held payment is SENT.
	import { toast } from 'svelte-sonner';
	import { Button } from '../ui/button/index.js';
	import { ConfirmDialog } from '../ui/confirm-dialog/index.js';
	import { Label } from '../ui/label/index.js';
	import { Textarea } from '../ui/textarea/index.js';
	import type { Enhance } from '../book/clients/form-enhance.js';

	let {
		runUid,
		isReview = false,
		action,
		enhance,
		approve = { title: 'Approve this?', description: 'The run goes ahead as soon as you approve.' },
		reject = {
			title: 'Reject this?',
			description: 'Nothing goes ahead. Say why — the person who raised it will see this.'
		},
		size = 'default',
		approveClass = '',
		ondecided
	}: {
		/** The run the decision is posted with — a gated child's own uid, not its parent's. */
		runUid: string;
		/** A book review decides through the reviews path (its lifecycle events live there). */
		isReview?: boolean;
		/** The form action (`?/decide`). */
		action: string;
		enhance: Enhance;
		approve?: { title: string; description: string };
		reject?: { title: string; description: string };
		size?: 'default' | 'sm';
		/** Extra classes on the Approve button — `flex-1` in a footer. */
		approveClass?: string;
		ondecided?: (decision: 'approved' | 'rejected') => void;
	} = $props();

	let approveOpen = $state(false);
	let rejectOpen = $state(false);
	let note = $state('');
	let approveForm = $state<HTMLFormElement | null>(null);
	let rejectForm = $state<HTMLFormElement | null>(null);

	function after(decision: 'approved' | 'rejected') {
		return () =>
			async ({
				result,
				update
			}: {
				result: { type: string; data?: { error?: string } };
				update: (opts?: { reset?: boolean }) => Promise<void>;
			}) => {
				if (result.type === 'success') {
					toast.success(decision === 'approved' ? 'Approved' : 'Rejected');
					note = '';
					ondecided?.(decision);
				} else {
					toast.error(
						String((result.type === 'failure' && result.data?.error) || 'That did not go through.')
					);
				}
				await update({ reset: false });
			};
	}
</script>

<form
	bind:this={rejectForm}
	method="POST"
	{action}
	use:enhance={after('rejected')}
	class="contents"
>
	<input type="hidden" name="run_uid" value={runUid} />
	<input type="hidden" name="decision" value="reject" />
	<input type="hidden" name="is_review" value={isReview ? 'true' : 'false'} />
	<input type="hidden" name="note" value={note} />
	<Button type="button" variant="destructive" {size} onclick={() => (rejectOpen = true)}>
		Reject
	</Button>
</form>
<form
	bind:this={approveForm}
	method="POST"
	{action}
	use:enhance={after('approved')}
	class={approveClass ? approveClass : 'contents'}
>
	<input type="hidden" name="run_uid" value={runUid} />
	<input type="hidden" name="decision" value="approve" />
	<input type="hidden" name="is_review" value={isReview ? 'true' : 'false'} />
	<Button
		type="button"
		{size}
		class={approveClass ? 'w-full' : ''}
		onclick={() => (approveOpen = true)}
	>
		Approve
	</Button>
</form>

<ConfirmDialog
	bind:open={approveOpen}
	title={approve.title}
	description={approve.description}
	confirmLabel="Approve"
	onconfirm={() => approveForm?.requestSubmit()}
/>

<ConfirmDialog
	bind:open={rejectOpen}
	title={reject.title}
	description={reject.description}
	confirmLabel="Reject"
	destructive
	confirmDisabled={!note.trim()}
	onconfirm={() => {
		if (!note.trim()) return;
		rejectForm?.requestSubmit();
	}}
>
	<div class="space-y-1">
		<Label for="decide-reject-note-{runUid}">Reason</Label>
		<Textarea id="decide-reject-note-{runUid}" bind:value={note} rows={3} required />
	</div>
</ConfirmDialog>
