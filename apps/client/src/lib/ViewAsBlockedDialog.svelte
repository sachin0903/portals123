<script lang="ts">
	import { Button, Dialog } from '@graphene/ui';
	import LockIcon from '@lucide/svelte/icons/lock';
	import { viewAsBlocked } from '$lib/view-as-guard.svelte';

	/**
	 * What a member of staff sees when they try to do something from inside a client's portal.
	 *
	 * The refusal has already happened at the API; this only reports it. One sentence and one
	 * way out — there is no dismiss-versus-cancel to distinguish, so the corner cross is off and
	 * a single button carries the whole interaction.
	 */
</script>

<Dialog.Root bind:open={viewAsBlocked.open}>
	<Dialog.Content showCloseButton={false} class="sm:max-w-sm">
		<div class="flex flex-col items-center gap-4 px-2 pt-4 pb-1 text-center">
			<span
				class="bg-brand/10 text-brand flex size-11 items-center justify-center rounded-full"
				aria-hidden="true"
			>
				<LockIcon class="size-5" />
			</span>
			<Dialog.Title class="text-base leading-snug font-semibold text-balance">
				You cannot take this action on behalf of the client
			</Dialog.Title>
			<Button variant="brand" class="mt-1 w-full" onclick={() => (viewAsBlocked.open = false)}>
				Got it
			</Button>
		</div>
	</Dialog.Content>
</Dialog.Root>
