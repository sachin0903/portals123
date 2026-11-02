<script lang="ts">
	import {
		DocumentViewer,
		InsightCover,
		KIND_SINGULAR,
		readingTime,
		stampOf,
		topicSlots
	} from '@graphene/ui';
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import { page } from '$app/state';
	import { backFor } from '$lib/nav';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const item = $derived(data.item);
	const fileSrc = $derived(`/insights/${item.uid}/file`);

	function publishedDate(iso: string | null): string {
		if (!iso) return '';
		const date = new Date(iso);
		return Number.isNaN(date.getTime())
			? ''
			: date.toLocaleDateString('en-GB', {
					day: 'numeric',
					month: 'long',
					year: 'numeric'
				});
	}

	// THE READER IS WHERE THE FIRM'S IDENTITY IS READ LONGEST, so it wears the firm's levers
	// plainly: the topic as a kicker in the brand ink under a brand rule, the headline in the
	// display face, pull quotes ruled in the brand colour (below), and a "Keep reading" rail of
	// covers in the firm's palette. The kicker also carries how long the piece takes — a client
	// deciding whether to read now or later asks that first.
	const kicker = $derived(
		item.category_name ?? (item.kind === 'guide' ? 'PDF guide' : KIND_SINGULAR[kindKey()])
	);
	function kindKey(): 'news' | 'article' | 'guide' {
		return item.kind === 'article' || item.kind === 'guide' ? item.kind : 'news';
	}
	const minutes = $derived(readingTime(item.body_html));
	const related = $derived(data.related);
	const slots = $derived(topicSlots(related));
	// The library page in the firm's own set — the same target the shell's back link resolves.
	const libraryHref = $derived(
		backFor(page.url.pathname, page.data.branding?.pages, page.data.back ?? null)?.href ?? null
	);
	const display = 'font-family: var(--font-display, var(--font-sans))';
</script>

<svelte:head><title>{item.title} · Insights</title></svelte:head>

<!-- No back link of its own: the shell draws the way back on every sub page (ClientShell
     `back`, resolved by $lib/nav::backFor). -->
<div class="mx-auto w-full {item.kind === 'guide' ? 'max-w-5xl' : 'max-w-4xl'}">
	<header class="border-brand max-w-3xl border-t-2 pt-5">
		<div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs font-medium">
			<span class="text-brand-strong tracking-[0.16em] uppercase">{kicker}</span>
			<span class="text-muted-foreground">
				{#if item.category_name}
					{KIND_SINGULAR[kindKey()]}
				{/if}
				{#if publishedDate(item.published_at)}
					{#if item.category_name}·{/if}
					<time datetime={item.published_at ?? undefined}>{publishedDate(item.published_at)}</time>
				{/if}
				{#if minutes}
					· {minutes}
				{/if}
			</span>
		</div>
		<h1
			class="mt-3 text-3xl leading-[1.1] font-medium tracking-tight text-balance sm:text-5xl"
			style={display}
		>
			{item.title}
		</h1>
		{#if item.summary}
			<p class="text-muted-foreground mt-4 max-w-2xl text-base leading-relaxed sm:text-lg">
				{item.summary}
			</p>
		{/if}
	</header>

	{#if item.hero_url}
		<img
			src={item.hero_url}
			alt=""
			class="mt-7 aspect-[16/7] w-full rounded-xl object-cover shadow-[var(--hero-shadow)]"
		/>
	{/if}

	{#if item.body_html}
		<article class="content-article mt-6 max-w-3xl">
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- `body_html` is the SANITISED snapshot: publish runs the author's markup through `app/content.py` (CSS inlined, then nh3 against a closed tag/attribute allowlist) and the client API serves only that column, never the editor schema. This is the opposite of the comms body preview, which is unsanitised firm-authored output and therefore takes a sandboxed iframe (docs/comms.md). -->
			{@html item.body_html}
		</article>
	{/if}

	{#if item.kind === 'guide' && item.has_file}
		<div class="mt-7 min-h-[70vh] overflow-hidden rounded-xl border bg-background">
			<DocumentViewer
				src={fileSrc}
				contentType={item.file_content_type ?? 'application/pdf'}
				name={item.title}
				class="h-full min-h-[70vh] w-full"
			/>
		</div>
	{/if}

	{#if related.length > 0}
		<!-- What to read next: the same topic first, then the newest of the rest (`relatedInsights`),
		     as covers in the firm's palette. The reader is otherwise a dead end — the way back is the
		     shell's chevron, which a client mid-article has scrolled away from. -->
		<section class="mt-12 border-t pt-6" aria-labelledby="keep-reading">
			<div class="flex items-baseline justify-between gap-3">
				<h2 id="keep-reading" class="text-[0.8125rem] font-semibold">Keep reading</h2>
				{#if libraryHref}
					<a
						href={libraryHref}
						class="text-muted-foreground hover:text-foreground tap-target inline-flex items-center gap-0.5 text-xs font-medium"
					>
						All insights
						<ArrowRightIcon class="size-3.5" aria-hidden="true" />
					</a>
				{/if}
			</div>
			<div class="mt-4 grid gap-4 sm:grid-cols-3">
				{#each related as next (next.id)}
					<a
						href={next.href}
						class="glass-panel group flex flex-col overflow-hidden rounded-xl transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5 hover:shadow-md"
					>
						<div class="aspect-[16/10] w-full overflow-hidden">
							<InsightCover item={next} slot={slots.get(next.source) ?? 0} />
						</div>
						<div class="flex flex-1 flex-col gap-1 p-4">
							<p class="text-brand-strong text-[0.7rem] font-medium tracking-[0.14em] uppercase">
								{next.source}
							</p>
							<p class="line-clamp-3 leading-snug font-medium tracking-tight" style={display}>
								{next.headline}
							</p>
							<p class="text-muted-foreground mt-auto pt-1 text-xs">{stampOf(next)}</p>
						</div>
					</a>
				{/each}
			</div>
		</section>
	{/if}
</div>

<style>
	.content-article :global(h1) {
		font-size: 1.5rem;
		font-weight: 600;
		margin: 1.5rem 0 0.75rem;
	}
	.content-article :global(h2) {
		font-size: 1.25rem;
		font-weight: 600;
		margin: 1.25rem 0 0.5rem;
	}
	.content-article :global(h3),
	.content-article :global(h4) {
		font-size: 1.05rem;
		font-weight: 600;
		margin: 1rem 0 0.4rem;
	}
	.content-article :global(p) {
		margin: 0.75rem 0;
	}
	.content-article :global(ul),
	.content-article :global(ol) {
		margin: 0.75rem 0 0.75rem 1.25rem;
	}
	.content-article :global(li) {
		margin: 0.25rem 0;
	}
	/* A pull quote is ruled in the firm's colour and set in its display face — the two levers a
	   firm has on its own words. `!important` because publish inlines the editor's stylesheet
	   (`border-left: 3px solid var(--primary)`), and the portal, not the snapshot, owns the look. */
	.content-article :global(blockquote) {
		border-left: 3px solid var(--brand) !important;
		padding-left: 1.25rem;
		margin: 1.5rem 0;
		color: var(--foreground);
		font-family: var(--font-display, var(--font-sans)) !important;
		font-size: 1.125rem;
		line-height: 1.6;
	}
	.content-article :global(a) {
		color: var(--brand-strong);
	}
	.content-article :global(img) {
		max-width: 100%;
		height: auto;
		border-radius: 0.75rem;
		margin: 1rem 0;
	}
	.content-article :global(a) {
		text-decoration: underline;
		text-underline-offset: 2px;
	}
	/* Published CSS is inlined so the snapshot is portable. That also means its desktop page
	   padding wins normal cascade rules. The portal owns the responsive surround, so remove the
	   editor's paper padding here with `!important`; otherwise 72px on each side leaves a phone
	   with a narrow ribbon of text. The editor's PAPER goes too: the snapshot paints
	   `background: var(--background)` on the shell, which on the portal's canvas (a gradient, not a
	   flat colour) drew a white slab hugging the text once the padding was gone. The text sits on
	   the page like the header above it. And the editor's own type stack (Inter) yields to the
	   portal's, so the body reads in the same face as its headline rather than as a pasted page. */
	.content-article :global(.article-shell) {
		max-width: none !important;
		min-height: 0 !important;
		padding: 0 !important;
		background: transparent !important;
		box-shadow: none !important;
		border: 0 !important;
		border-radius: 0 !important;
		font-family: inherit !important;
	}
	/* The metadata header above is canonical. Older Word imports included the document title as
	   their first body node, producing the same headline twice. Hide only that leading heading;
	   headings anywhere in the article remain untouched. */
	.content-article :global(.article-shell > h1:first-child),
	.content-article :global(.article-shell > h2:first-child),
	.content-article :global(.article-shell > .article-kicker:first-child),
	.content-article :global(.article-shell > .article-kicker:first-child + h1) {
		display: none !important;
	}
	@media (max-width: 640px) {
		.content-article :global(.article-shell) {
			font-size: 1rem !important;
			line-height: 1.7 !important;
		}
		.content-article :global(.article-shell h1) {
			font-size: 1.75rem !important;
		}
		.content-article :global(.article-shell h2) {
			margin-top: 1.75rem !important;
			font-size: 1.375rem !important;
		}
		.content-article :global(.article-shell h3) {
			margin-top: 1.5rem !important;
			font-size: 1.125rem !important;
		}
		.content-article :global(.article-shell .article-lede) {
			margin-bottom: 1.5rem !important;
			font-size: 1.0625rem !important;
		}
		.content-article :global(.article-shell blockquote) {
			margin: 1.5rem 0 !important;
			padding-left: 1rem !important;
			font-size: 1.125rem !important;
		}
	}
</style>
