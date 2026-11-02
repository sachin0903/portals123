<script lang="ts">
	import {
		Button,
		buttonVariants,
		Card,
		CardContent,
		cn,
		DocumentViewer,
		Separator,
		Sheet,
		StatusBadge,
		type StatusTone
	} from '@graphene/ui';
	import { downloadName } from '@graphene/ui/book/documents';
	import { page } from '$app/state';
	import { formatDate, formatSize } from '$lib/format';
	import { documentTypeLabel } from '$lib/documents';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import PanelRightOpenIcon from '@lucide/svelte/icons/panel-right-open';

	let { data } = $props();
	const doc = $derived(data.doc);
	const preview = $derived({
		src: data.previewUrl ?? undefined,
		contentType: data.previewContentType ?? undefined
	});

	// Client-facing status, mirroring the StatusBadge chrome on the platform/admin detail
	// views — derived from the document's attention flags rather than a stored state.
	const status = $derived.by<{ tone: StatusTone; label: string; pulse: boolean }>(() => {
		if (doc.unread) return { tone: 'info', label: 'Unread', pulse: false };
		return { tone: 'success', label: 'Available', pulse: false };
	});

	let detailsOpen = $state(false);
</script>

<svelte:head><title>{doc.name} · {page.data.appName ?? 'Graphene'}</title></svelte:head>

<!-- The sidebar body is shared: rendered inline beside the document on desktop, and inside a
     pull-out Sheet on mobile (where the document gets the full screen). -->
{#snippet sidebarBody()}
	<div class="space-y-4">
		<!-- Title + status -->
		<div>
			<h1 class="text-xl font-semibold leading-snug">{doc.name}</h1>
			<p class="text-muted-foreground mt-0.5 text-sm">{documentTypeLabel(doc.category)}</p>
			<div class="mt-2">
				<StatusBadge tone={status.tone} pulse={status.pulse}>{status.label}</StatusBadge>
			</div>
		</div>

		<!-- Actions — download the document itself (absent when there are no bytes yet).
		     `downloadName` with the REAL content type, not `doc.name`: a generated document's name is a
		     title with no extension, and the `download` attribute is where the browser takes the
		     filename from — so this saved a file the OS could not type or open. This page holds
		     `previewContentType`, so the extension here is derived from the actual MIME rather than
		     from the row's format tag, which defaults to pdf for anything it does not recognise. -->
		{#if preview.src}
			<Button
				variant="secondary"
				size="sm"
				class="w-full"
				href={preview.src}
				download={downloadName(doc, data.previewContentType)}
				target="_blank"
				rel="noopener"
			>
				<DownloadIcon class="size-4" /> Download
			</Button>
		{:else}
			<Button variant="secondary" size="sm" class="w-full" disabled>
				<DownloadIcon class="size-4" /> Download
			</Button>
		{/if}

		<!-- Details — versions/tags/activity tabs were placeholders with no real data; they return
		     when the backend models them. -->
		<Card>
			<CardContent class="space-y-2 text-sm">
				<div class="flex justify-between">
					<span class="text-muted-foreground">Category</span><span
						>{documentTypeLabel(doc.category)}</span
					>
				</div>
				<div class="flex justify-between">
					<span class="text-muted-foreground">Size</span><span>{formatSize(doc.sizeBytes)}</span>
				</div>
				<div class="flex justify-between">
					<span class="text-muted-foreground">Added</span><span>{formatDate(doc.createdAt)}</span>
				</div>
				<div class="flex justify-between gap-3">
					<span class="text-muted-foreground shrink-0">File name</span><span
						class="min-w-0 truncate"
						title={doc.name}>{doc.name}</span
					>
				</div>
			</CardContent>
		</Card>

		<Separator />
		<p class="text-muted-foreground text-xs leading-relaxed">
			Spotted something wrong with this document? Contact your adviser and they'll put it right.
		</p>
	</div>
{/snippet}

<!-- Document + sidebar. Width stays inside the client shell's centred column (only the
     surrounding padding is cancelled). The row is pinned to the full page height so the
     document fills the page; on desktop the sidebar sits alongside, on mobile the document is
     framed with breathing room and the sidebar lives in a pull-out Sheet. -->
<div
	class="relative -mx-4 -mt-6 -mb-[var(--shell-pb)] flex h-[calc(100svh-4rem-var(--shell-back-h,0px))] sm:-mx-6 lg:-my-10"
>
	<!-- Viewer — the page floats on the app's gradient backdrop (transparent surround). The
	     bottom padding is the shell's own clearance (`--shell-pb`): the safe area on a phone —
	     this is a sub page, so the floating tab bar is hidden — nothing on desktop. -->
	<div class="relative min-w-0 flex-1 overflow-hidden pb-[var(--shell-pb)] lg:pb-0">
		<DocumentViewer
			src={preview.src}
			contentType={preview.contentType}
			name={doc.name}
			class="h-full w-full bg-transparent"
		/>

		<!-- No floating back button: the shell draws the way back on every sub page (ClientShell
		     `back`) — in the top bar on a phone, above the content on desktop. -->

		<!-- Mobile-only: details panel, opened from the top-right -->
		<Sheet.Root bind:open={detailsOpen}>
			<Sheet.Trigger
				class={cn(
					buttonVariants({ variant: 'outline', size: 'sm' }),
					'bg-card/85 hover:bg-card tap-target absolute right-3 top-3 z-20 rounded-full shadow-sm backdrop-blur lg:hidden'
				)}
			>
				<PanelRightOpenIcon class="size-4" /> Details
			</Sheet.Trigger>
			<Sheet.Content side="right" class="w-[88%] gap-0 p-5 sm:!max-w-sm">
				<Sheet.Title class="sr-only">Document details</Sheet.Title>
				<div class="min-h-0 flex-1 overflow-y-auto">
					{@render sidebarBody()}
				</div>
			</Sheet.Content>
		</Sheet.Root>
	</div>

	<!-- Desktop sidebar — scrolls alongside the document -->
	<aside class="bg-card hidden w-[360px] shrink-0 overflow-y-auto border-l lg:block">
		<div class="p-5">{@render sidebarBody()}</div>
	</aside>
</div>
