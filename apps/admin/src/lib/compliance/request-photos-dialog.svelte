<script lang="ts">
	// Ask the client for new photos instead of deciding their identity check: they get a new try, and
	// the check stays as it is. The form posts the page's `requestPhotos` action. Mounted per open, so
	// each open starts empty.
	import { enhance } from '$app/forms';
	import {
		Alert,
		AlertDescription,
		Button,
		Checkbox,
		Dialog,
		Label,
		SelectMenu,
		Textarea
	} from '@graphene/ui';
	import {
		clientPhotoText,
		photoReasonsFor,
		type AmlCheckDetail,
		type PhotoChoice,
		type PhotoRequestReason
	} from '$lib/aml';
	import { submit } from './submit';

	let {
		detail,
		clientName,
		error = null,
		onclose
	}: {
		/** The identity check the client gets a new try on. */
		detail: AmlCheckDetail;
		clientName: string;
		/** The `requestPhotos` action's refusal, shown above the buttons. */
		error?: string | null;
		/** Cancelled, dismissed, or the request was sent. */
		onclose: () => void;
	} = $props();

	let photoChoices = $state<PhotoChoice[]>([]);
	let photoReason = $state('');
	let photoNote = $state('');
	let sendingPhotoRequest = $state(false);

	function togglePhoto(choice: PhotoChoice, checked: boolean) {
		photoChoices = checked ? [...photoChoices, choice] : photoChoices.filter((c) => c !== choice);
		// The reason lists don't fully overlap, so a choice change can leave the picked reason unfit.
		if (photoReason && !photoReasonItems.some((r) => r.value === photoReason)) photoReason = '';
	}

	const photoReasonItems = $derived(
		photoReasonsFor(photoChoices).map((r) => ({ value: r.value, label: r.label }))
	);
</script>

<Dialog.Root open onOpenChange={(o) => !o && onclose()}>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>Ask for new photos</Dialog.Title>
			<Dialog.Description>
				{clientName} gets a new try — try {detail.check.attempt + 1}. This does not decide the
				check.
			</Dialog.Description>
		</Dialog.Header>
		<form
			method="POST"
			action="?/requestPhotos"
			use:enhance={submit(
				'New photos requested.',
				(b) => (sendingPhotoRequest = b),
				() => onclose()
			)}
			class="space-y-4"
		>
			<input type="hidden" name="check" value={detail.check.uid} />
			{#each photoChoices as choice (choice)}
				<input type="hidden" name="photos" value={choice} />
			{/each}

			<div class="space-y-2">
				<Label>Which photos</Label>
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox
						checked={photoChoices.includes('document')}
						onCheckedChange={(v) => togglePhoto('document', v === true)}
					/>
					<span>
						Document
						<span class="text-muted-foreground block text-xs">Both sides</span>
					</span>
				</label>
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox
						checked={photoChoices.includes('selfie')}
						onCheckedChange={(v) => togglePhoto('selfie', v === true)}
					/>
					<span>Selfie</span>
				</label>
			</div>

			<div class="space-y-2">
				<Label for="photo-reason">What the client is told</Label>
				<SelectMenu
					id="photo-reason"
					name="reason"
					items={photoReasonItems}
					bind:value={photoReason}
					placeholder="Choose what the client will be told…"
					required
				/>
			</div>

			<Alert variant="info">
				<AlertDescription>
					{#if photoReason}
						The client will see: “{clientPhotoText(photoReason as PhotoRequestReason)}”
					{:else}
						The client will see the message you choose.
					{/if}
				</AlertDescription>
			</Alert>

			<div class="space-y-2">
				<Label for="photo-note">Note (internal — never shown to the client)</Label>
				<Textarea
					id="photo-note"
					name="note"
					bind:value={photoNote}
					rows={3}
					placeholder="What was wrong with the photos, for the record."
				/>
			</div>

			{#if error}
				<p class="text-destructive text-xs">{error}</p>
			{/if}

			<Dialog.Footer>
				<Button type="button" variant="outline" onclick={() => onclose()}>Cancel</Button>
				<Button
					type="submit"
					disabled={sendingPhotoRequest ||
						!photoChoices.length ||
						!photoReason ||
						!photoNote.trim()}
				>
					{sendingPhotoRequest ? 'Sending…' : 'Ask for new photos'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
