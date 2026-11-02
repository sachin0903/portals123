<script lang="ts">
	import type { Snippet } from 'svelte';
	import * as Dialog from '../dialog/index.js';
	import { Button } from '../button/index.js';

	/**
	 * The in-product replacement for `window.confirm()`: a small modal with a clear question,
	 * an explicit destructive style when the action can't be undone, and a busy state while the
	 * confirmed action runs. Control it with `bind:open`; `onconfirm` may be async — the dialog
	 * shows a pending label and closes itself when the promise settles.
	 */
	let {
		open = $bindable(false),
		title,
		description,
		confirmLabel = 'Confirm',
		cancelLabel = 'Cancel',
		pendingLabel = 'Working…',
		destructive = false,
		confirmDisabled = false,
		onconfirm,
		children
	}: {
		open?: boolean;
		title: string;
		description?: string;
		confirmLabel?: string;
		cancelLabel?: string;
		pendingLabel?: string;
		/** Style the confirm button as destructive (deletes, cancels, deactivations). */
		destructive?: boolean;
		/** Hold the confirm button while the body's own input is not yet acceptable (a required
		 *  reason, say). The cancel button is never held. */
		confirmDisabled?: boolean;
		onconfirm: () => void | Promise<void>;
		/** Optional extra body content between the description and the buttons. */
		children?: Snippet;
	} = $props();

	let busy = $state(false);

	async function confirm() {
		busy = true;
		try {
			await onconfirm();
			open = false;
		} finally {
			busy = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>{title}</Dialog.Title>
			{#if description}
				<Dialog.Description>{description}</Dialog.Description>
			{/if}
		</Dialog.Header>
		{@render children?.()}
		<Dialog.Footer>
			<Button type="button" variant="outline" disabled={busy} onclick={() => (open = false)}>
				{cancelLabel}
			</Button>
			<Button
				type="button"
				variant={destructive ? 'destructive' : 'default'}
				disabled={busy || confirmDisabled}
				onclick={confirm}
			>
				{busy ? pendingLabel : confirmLabel}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
