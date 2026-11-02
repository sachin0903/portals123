<script lang="ts">
	// The box you type into, shared by every staff surface that can send: the platform inbox's
	// direct messages, its group feed, and the client record's Messages section. What a send POSTS
	// to differs per surface, so that stays with the caller — `submit` is its handler and `draft`
	// and `files` are bound, which is also how a caller clears them after a send lands.
	import { Button, Textarea, cn } from '@graphene/ui';
	import FileInputIcon from '@lucide/svelte/icons/file-input';
	import PaperclipIcon from '@lucide/svelte/icons/paperclip';
	import SendIcon from '@lucide/svelte/icons/send';
	import XIcon from '@lucide/svelte/icons/x';
	import { ATTACHMENT_ACCEPT } from './messages.js';
	import { formatBytes } from '../../../bytes.js';

	let {
		draft = $bindable(''),
		files = $bindable([]),
		placeholder = 'Type a message... (Ctrl+Enter to send)',
		error = '',
		sending = false,
		submit,
		sendLabel = 'Send message',
		onRequest,
		requestLabel = 'Request a document from this client',
		enterSends = false,
		compact = false,
		class: className
	}: {
		draft?: string;
		/** Staged files. All of them ride ONE message (0123): a note and the three documents it is
		 *  about are one thing, and sending them as three bare messages made the reader reconstruct
		 *  that. */
		files?: File[];
		placeholder?: string;
		/** A failed send, stated above the box that produced it. */
		error?: string;
		sending?: boolean;
		submit: (event: SubmitEvent) => void;
		sendLabel?: string;
		/** Absent ⇒ no "ask for a document" button (the reader lacks `documents:send`, or the
		 *  surface has nobody to ask). */
		onRequest?: () => void;
		requestLabel?: string;
		/**
		 * Enter sends, Shift+Enter breaks a line — on a device with a fine pointer (a keyboard
		 * in front of a screen). The staff surfaces keep Ctrl+Enter, because an adviser's reply
		 * is often several paragraphs and a stray Enter sending half of one is the costlier
		 * mistake; a client's chat is the messaging app they already use, where Enter sends. On a
		 * touch device Enter stays a newline either way — the keyboard's return key is for lines,
		 * and the Send button is right there.
		 */
		enterSends?: boolean;
		/**
		 * One row — paperclip, the box, Send — the pill every chat app docks at the foot of a
		 * conversation. The default is the staff shape: a taller box with the actions on a row
		 * beneath, room for the "ask for a document" button, built for replies of several
		 * paragraphs. A client's turn is usually a line or two, and on a phone the two-row box
		 * took a third of the screen before a word was typed.
		 */
		compact?: boolean;
		class?: string;
	} = $props();

	function sendsOnEnter(e: KeyboardEvent): boolean {
		if (e.key !== 'Enter') return false;
		if (e.ctrlKey || e.metaKey) return true;
		if (!enterSends || e.shiftKey || e.isComposing) return false;
		return typeof window !== 'undefined' && window.matchMedia('(pointer: fine)').matches;
	}

	let fileInput = $state<HTMLInputElement | null>(null);

	function stageFiles(event: Event): void {
		// The WHOLE set: the input is `multiple`, and taking `[0]` would silently drop the rest.
		const picked = (event.currentTarget as HTMLInputElement).files;
		if (picked) files = [...files, ...Array.from(picked)];
		// Cleared so choosing the SAME file again still fires `change` — otherwise re-attaching a
		// file you just removed does nothing at all.
		if (fileInput) fileInput.value = '';
	}
</script>

<form
	onsubmit={submit}
	class={cn(
		'border-input relative border bg-transparent',
		// The pill's radius is a multiple of --radius (≈ Tailwind's 3xl at the default), not the
		// fixed step: a firm's radius — and the sharp surface preset — must reach it like every
		// other corner (token-hygiene.test.ts). Not rounded-full: the box grows with the text, and
		// a tall stadium reads as a blob.
		compact ? 'rounded-[calc(var(--radius)*2.4)] px-1.5 py-1.5' : 'rounded-xl p-2 sm:p-3',
		className
	)}
>
	{#if error}
		<p class="text-destructive px-2 pb-1 text-sm">{error}</p>
	{/if}
	<!-- "Ask for a document" sits in the composer's TOP-RIGHT corner. Absolutely positioned rather
	     than placed in a row, so it costs the composer no height: the textarea's placeholder starts
	     top-LEFT, leaving that corner free. In the bottom row it sat diagonally opposite Send and
	     read as a second way to send. -->
	{#if onRequest}
		<Button
			type="button"
			variant="ghost"
			size="icon"
			class="text-muted-foreground hover:text-foreground absolute top-2 right-2 size-8 sm:top-3 sm:right-3"
			aria-label={requestLabel}
			onclick={onRequest}
		>
			<FileInputIcon class="size-4" />
		</Button>
	{/if}
	{#if !compact}
		{@render box()}
	{/if}
	{#if files.length > 0}
		<!-- Staged files, each removable. Several ride ONE message, so this list IS the message you
		     are about to send — seeing it before sending is the only chance to correct it. -->
		<div class="flex flex-wrap gap-2 px-2 pb-1">
			{#each files as file, i (`${file.name}-${i}`)}
				<span class="bg-muted flex max-w-[16rem] items-center gap-1.5 rounded-lg px-2 py-1 text-xs">
					<PaperclipIcon class="text-muted-foreground size-3 shrink-0" />
					<span class="truncate">{file.name}</span>
					<span class="text-muted-foreground shrink-0">{formatBytes(file.size)}</span>
					<!-- A 12px × in a 24px chip; `tap-target` gives it the 44px a thumb needs. The
					     chip's own label keeps the next × at least a word away. -->
					<button
						type="button"
						class="text-muted-foreground hover:text-foreground tap-target flex size-6 shrink-0 items-center justify-center"
						aria-label={`Remove ${file.name}`}
						onclick={() => (files = files.filter((_, n) => n !== i))}
					>
						<XIcon class="size-3" />
					</button>
				</span>
			{/each}
		</div>
	{/if}
	<div class={cn('flex gap-2', compact ? 'items-end gap-1' : 'mt-1 items-center')}>
		<!-- `multiple`: the picker takes a set, and the whole set rides on one message. -->
		<input
			bind:this={fileInput}
			type="file"
			multiple
			accept={ATTACHMENT_ACCEPT}
			class="hidden"
			onchange={stageFiles}
			aria-hidden="true"
			tabindex="-1"
		/>
		<Button
			type="button"
			variant="ghost"
			size="icon"
			class={cn(
				'text-muted-foreground hover:text-foreground shrink-0',
				compact ? 'mb-1 size-9 rounded-full' : 'size-8'
			)}
			aria-label="Attach files"
			onclick={() => fileInput?.click()}
		>
			<PaperclipIcon class="size-4" />
		</Button>
		{#if compact}
			{@render box()}
		{/if}
		<Button
			type="submit"
			size="icon"
			class={cn('ml-auto shrink-0 rounded-full', compact && 'mb-1 size-9')}
			aria-label={sendLabel}
			disabled={(!draft.trim() && files.length === 0) || sending}
		>
			<SendIcon class="size-4" />
		</Button>
	</div>
</form>

{#snippet box()}
	<Textarea
		bind:value={draft}
		{placeholder}
		rows={1}
		class={cn(
			'max-h-40 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0 dark:bg-transparent',
			// min-h-11: a field is tapped to focus it, and has no pseudo-element to extend. The
			// single line is CENTRED in those 44px (12 + 20 + 12), and the two 36px buttons beside
			// it, bottom-aligned so they stay at the foot as the box grows, carry `mb-1` so their
			// centres meet the line's at rest — without it the text sat visibly high in the pill.
			compact ? 'min-h-11 min-w-0 flex-1 px-2 py-3 leading-5' : 'min-h-12 px-2'
		)}
		onkeydown={(e) => {
			if (sendsOnEnter(e)) {
				e.preventDefault();
				submit(new SubmitEvent('submit'));
			}
		}}
	/>
{/snippet}
