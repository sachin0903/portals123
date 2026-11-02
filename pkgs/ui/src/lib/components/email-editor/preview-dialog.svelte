<script lang="ts">
	// The editor's CURRENT content as a sample send — merge fields filled, CSS inlined, the firm's real brand. Server-rendered (POST to the route's /preview proxy) and re-fetched on every open, so it reflects the latest canvas. Nothing is ever sent.
	import { untrack } from 'svelte';
	import { EMAIL_DEVICES } from './create.js';
	import { mergeFieldForPath } from './merge-fields.js';
	import { Alert, AlertDescription } from '../ui/alert/index.js';
	import * as Avatar from '../ui/avatar/index.js';
	import { Badge } from '../ui/badge/index.js';
	import { Button } from '../ui/button/index.js';
	import * as Dialog from '../ui/dialog/index.js';
	import Segmented from '../ui/segmented/segmented.svelte';
	import { Separator } from '../ui/separator/index.js';
	import { Skeleton } from '../ui/skeleton/index.js';
	import { EmailPreviewFrame } from '../email/index.js';
	import { initials } from '../../initials.js';
	import RefreshCwIcon from '@lucide/svelte/icons/refresh-cw';
	import {
		createPreviewFetcher,
		type EmailPreview,
		type PreviewAudience,
		type PreviewPayload
	} from './preview';

	let {
		open = $bindable(false),
		previewUrl,
		senderName = 'Your firm',
		audience = 'client',
		content
	}: {
		open?: boolean;
		/** Renders a sample preview and never sends; injected so both surfaces use their own route. */
		previewUrl: string;
		senderName?: string;
		/** DISPLAY only: the request carries it inside `content()`, so this names the brand rather than choosing it. The chooser is in the editor's top bar, which governs the test send too. */
		audience?: PreviewAudience;
		/** Read at fetch time, so the preview is never stale; it returns the whole payload, so this dialog cannot omit a field. */
		content: () => PreviewPayload;
	} = $props();

	let loading = $state(false);
	let errorMsg = $state<string | null>(null);
	let preview = $state<EmailPreview | null>(null);
	let device = $state<'desktop' | 'mobile'>('desktop');

	// The request contract and the stale-response guard live in the shared fetcher, so a second preview surface cannot reimplement them differently. Re-derived per template: a different template is a different request sequence.
	const fetchPreview = $derived(createPreviewFetcher(previewUrl));

	async function load(): Promise<void> {
		loading = true;
		errorMsg = null;
		const res = await fetchPreview(content());
		// A newer request now owns the state, `loading` included — clearing it here would hide its spinner.
		if (res.stale) return;
		if ('error' in res) {
			errorMsg = res.error;
			preview = null;
		} else {
			preview = res.data;
		}
		loading = false;
	}

	// Fetch only on OPEN: `untrack` keeps the effect from subscribing to the `content()` read inside load(), which would re-fetch on every debounced canvas edit.
	// `audience` IS tracked, because the previous brand under a label naming the new one is worse than no label — the `&&` reads it only once open, and the modal covers the chooser, so this is a guard, not a live path.
	$effect(() => {
		if (open && audience) untrack(() => load());
	});

	// Read from `EMAIL_DEVICES`, which calls itself the one place these widths are written down: the
	// dialog and the canvas are the same two devices, and a preview that checks a narrower phone than
	// the editor shows is a check of something the author never sees.
	const frameWidth = $derived(
		Number.parseInt(EMAIL_DEVICES.find((d) => d.id === device)?.width ?? '640px', 10)
	);
	// The whole ENVELOPE, not just the body: on a phone the inbox chrome wraps and truncates as the body does, and the subject running out of room is one of the things a mobile check is for. `+ 32` is the body wrapper's own `p-4` a side.
	const envelopeWidth = $derived(frameWidth + 32);

	// Fixed, not derived: the real To and timestamp are decided at send time, so a live-looking value would imply a precision the preview lacks.
	// The sampled recipient the PREVIEW renders against (`email_preview._SAMPLE['full_name']`, whose
	// value reaches here through the merge-field catalogue). A name of its own would put one person in
	// the envelope and another in the greeting.
	const SAMPLE_RECIPIENT = mergeFieldForPath('full_name')?.example ?? '';
	const SAMPLE_TIME = '10:32';
	// From the injected `senderName` (the app resolves it from branding, as the shell does) and never scraped out of the rendered body — the first text node there is the email's own heading.
	const senderInitials = $derived(initials(senderName, 'F'));
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="flex max-h-[90vh] flex-col gap-0 p-0 sm:max-w-3xl">
		<Dialog.Header class="border-b px-5 py-4">
			<div class="flex items-center gap-2">
				<Dialog.Title>Preview</Dialog.Title>
				<Badge variant="outline">Sample</Badge>
				<!-- Chosen in the editor's top bar; named here because the two brands can look similar enough that an author cannot otherwise tell which they are judging. -->
				<Badge variant="outline">{audience === 'staff' ? 'Staff brand' : 'Client brand'}</Badge>
			</div>
			<!-- Which brand, and that nothing is sent, are already on the badges beside the title and in
			     the footer line, so the description says only what the badges cannot. -->
			<Dialog.Description>
				How this email looks with sample values filled in and styles inlined.
			</Dialog.Description>
		</Dialog.Header>

		<!-- Segmented, not a pair of Buttons: two buttons whose variant encodes selection is the pattern docs/design-language.md names as wrong. -->
		<div class="flex items-center gap-2 border-b px-5 py-2">
			<Segmented
				bind:value={device}
				options={[
					{ value: 'desktop', label: 'Desktop' },
					{ value: 'mobile', label: 'Mobile' }
				]}
			/>
			<Button
				type="button"
				size="sm"
				variant="ghost"
				class="ml-auto"
				disabled={loading}
				onclick={load}
			>
				<RefreshCwIcon class="mr-1.5 size-4 {loading ? 'animate-spin' : ''}" /> Refresh
			</Button>
		</div>

		<div class="bg-muted/40 min-h-0 flex-1 overflow-auto p-5">
			{#if errorMsg}
				<Alert variant="destructive"><AlertDescription>{errorMsg}</AlertDescription></Alert>
			{:else}
				{#if preview?.missing.length}
					<Alert class="mb-4">
						<AlertDescription>
							These fields had no sample value and rendered empty:
							<span class="font-medium">{preview.missing.join(', ')}</span>. Check the token
							spelling if that's unexpected.
						</AlertDescription>
					</Alert>
				{/if}

				<!-- A mail-client envelope, not a bare iframe, so an author can judge "does this look right in an inbox". All SAMPLE framing — the real From/To are decided at send time. -->
				<div
					class="bg-card mx-auto overflow-hidden rounded-xl border shadow-sm transition-[max-width] duration-150"
					style:max-width={`${envelopeWidth}px`}
					class:animate-pulse={loading && !preview}
				>
					<div class="flex items-start gap-3 px-4 py-3">
						<Avatar.Root class="size-9 shrink-0">
							<Avatar.Fallback class="bg-brand text-brand-foreground text-xs font-semibold">
								{senderInitials}
							</Avatar.Fallback>
						</Avatar.Root>
						<div class="min-w-0 flex-1">
							{#if loading && !preview}
								<Skeleton class="h-4 w-32" />
								<Skeleton class="mt-1.5 h-3 w-40" />
							{:else}
								<div class="flex items-baseline gap-2">
									<p class="text-foreground truncate text-sm font-semibold">{senderName}</p>
									<span class="text-muted-foreground shrink-0 text-xs">{SAMPLE_TIME}</span>
								</div>
								<p class="text-muted-foreground truncate text-xs">to {SAMPLE_RECIPIENT}</p>
							{/if}
						</div>
					</div>

					<Separator />

					<div class="space-y-1 px-4 py-3">
						{#if loading && !preview}
							<Skeleton class="h-5 w-3/4" />
							<Skeleton class="h-3.5 w-1/2" />
						{:else}
							<p class="text-foreground text-base font-semibold break-words">
								{preview?.subject || '(no subject)'}
							</p>
							<p class="text-muted-foreground text-sm break-words">
								{preview?.preheader || 'No inbox preview line set'}
							</p>
						{/if}
					</div>

					<Separator />

					<!-- The frame, and the sandbox that makes rendering author HTML safe at all, is @graphene/ui/email's, so no second surface can render it a laxer way. -->
					<div class="bg-muted/40 flex justify-center p-4">
						{#if loading && !preview}
							<div class="w-full max-w-[640px] space-y-3 rounded-lg border bg-doc-paper p-6">
								<Skeleton class="h-4 w-24" />
								<Skeleton class="h-6 w-2/3" />
								<Skeleton class="h-3 w-full" />
								<Skeleton class="h-3 w-11/12" />
								<Skeleton class="h-3 w-4/5" />
								<Skeleton class="mt-4 h-9 w-36 rounded-md" />
								<Skeleton class="mt-4 h-3 w-1/3" />
							</div>
						{:else if preview}
							<EmailPreviewFrame
								html={preview.html}
								width={frameWidth}
								title="Email preview"
								class="bg-doc-paper h-[52vh] rounded-lg border shadow-sm"
							/>
						{/if}
					</div>
				</div>

				<p class="text-muted-foreground mt-3 text-center text-xs">
					Sample values, styles inlined. Nothing is sent.
				</p>
			{/if}
		</div>

		<!-- NO FOOTER: a bar whose only control is Close repeats the corner ✕, Escape and click-outside, and charges ~50px of the height the email needs. -->
	</Dialog.Content>
</Dialog.Root>
