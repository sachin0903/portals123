<script lang="ts" module>
	import type { ClientDocument, DocumentCategory } from './types.js';

	// Stable category order for the index — same buckets, same order, as documents-list's
	// grouped view (its grouping lives in an instance-script $derived, so it isn't importable;
	// this module export keeps the shared semantics unit-testable instead).
	const ORDER: DocumentCategory[] = [
		'Statements',
		'Illustrations',
		'Agreements',
		'Tax',
		'Correspondence'
	];

	/**
	 * Bucket documents by category in the canonical order. Anything with an unrecognised
	 * category files under 'Other', rendered last — server drift shows up as an odd bucket,
	 * never as a silently missing document (the documents-list rule, kept in lockstep).
	 */
	export function groupDocuments(
		documents: ClientDocument[]
	): { category: string; items: ClientDocument[] }[] {
		const known = ORDER.map((category) => ({
			category: category as string,
			items: documents.filter((d) => d.category === category)
		})).filter((g) => g.items.length > 0);
		const other = documents.filter((d) => !(ORDER as readonly string[]).includes(d.category));
		return other.length > 0 ? [...known, { category: 'Other', items: other }] : known;
	}
</script>

<script lang="ts">
	let { documents }: { documents: ClientDocument[] } = $props();

	// The 'index' documents look — the ruled register. Zero chrome: small-caps category
	// headers over a top hairline, then hairline rows of name / date / size / format. No
	// icons, no badges, no panels — pure ledger typography, so it sits identically on the
	// card and ledger finishes.
	const groups = $derived(groupDocuments(documents));
</script>

<!-- The size column sheds by CONTAINER width (the block's grid span), not viewport — the
     established investment-block pattern. -->
<div class="@container space-y-7">
	{#each groups as group (group.category)}
		<section>
			<h2
				class="text-muted-foreground flex items-baseline gap-2 text-xs font-semibold tracking-[0.16em] uppercase"
			>
				{group.category}
				<span class="text-muted-foreground/70 font-normal tracking-normal normal-case">
					· {group.items.length}
				</span>
			</h2>
			<div class="border-border mt-2 border-t"></div>
			<ul class="divide-y">
				{#each group.items as doc (doc.id)}
					<li>
						<!-- min-h-11 keeps the whole row a ≥44px touch target even at text-sm. -->
						<a
							href="/documents/{doc.id}"
							class="hover:bg-accent/40 flex min-h-11 items-center gap-4 py-2 transition-colors"
						>
							<p class="min-w-0 flex-1 truncate text-sm font-medium">{doc.name}</p>
							<span class="text-muted-foreground shrink-0 text-xs tabular-nums">{doc.date}</span>
							<span class="text-muted-foreground @lg:inline hidden shrink-0 text-xs tabular-nums">
								{doc.size}
							</span>
							<!-- The format as a bare small-caps tag — a Badge would be chrome this
							     look has sworn off. -->
							<span class="text-muted-foreground shrink-0 text-xs tracking-[0.08em] uppercase">
								{doc.format}
							</span>
						</a>
					</li>
				{/each}
			</ul>
		</section>
	{/each}
</div>
