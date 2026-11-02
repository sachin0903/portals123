<script lang="ts">
	// EMAIL CONTEXT — what this email is given, and what else it can be given. NOT a list of tokens to
	// insert: that is the rail's field flyout, which is where the caret is, and the same list in two
	// places is how two lists start disagreeing about what a field is called. This panel answers the
	// question the flyout cannot: where do these come from, and what would ticking another one add.
	import { Badge } from '../ui/badge/index.js';
	import { Button } from '../ui/button/index.js';
	import * as Dialog from '../ui/dialog/index.js';
	import { SectionHeading } from '../ui/section-heading/index.js';
	import { Checkbox } from '../ui/checkbox/index.js';
	import { BY_SUBJECT, SUBJECT_LABELS, fieldsFor, grouped, type SubjectKind } from './scopes.js';

	let {
		open = $bindable(false),
		subjects = [],
		onsubjects
	}: {
		open?: boolean;
		/** What the email is ABOUT — decides which event fields it can use. */
		subjects?: readonly SubjectKind[];
		/** SAVES the claim. Absent ⇒ the subjects are shown but not editable (a read-only mount). */
		onsubjects?: (next: SubjectKind[]) => void;
	} = $props();

	const SUBJECT_ORDER = Object.keys(BY_SUBJECT) as SubjectKind[];

	// A DRAFT of the claim, not the claim itself: this is a SETTING, and a setting is committed on
	// purpose. Ticking shows what it would give you (the list below grows at once); Save writes it.
	// Re-seeded whenever the panel opens, so an abandoned edit does not come back.
	let pending = $state<SubjectKind[]>([]);
	let seeded = $state(false);
	$effect(() => {
		if (!open) {
			seeded = false;
			return;
		}
		if (seeded) return;
		seeded = true;
		pending = [...subjects];
	});

	const changed = $derived(
		pending.length !== subjects.length || pending.some((k) => !subjects.includes(k))
	);

	function toggleSubject(kind: SubjectKind): void {
		pending = pending.includes(kind) ? pending.filter((k) => k !== kind) : [...pending, kind];
	}

	function save(): void {
		onsubjects?.([...pending]);
		open = false;
	}

	/** The event details the TICKED contexts carry, so the checkboxes visibly do something. */
	const scopeGroups = $derived(grouped(fieldsFor(pending)));
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-xl">
		<Dialog.Header>
			<Dialog.Title>What this email is about</Dialog.Title>
			<!-- No description: what this EMAIL is for belongs on the listing, and repeating it above a
			     list of details described the page rather than the panel. -->
		</Dialog.Header>

		<div class="max-h-[26rem] space-y-4 overflow-y-auto [scrollbar-gutter:stable]">
			<!-- THE CONTEXT, then the CHOICES that widen it. One box, because they are one idea: the email
			     is always given the firm and the act, and what else it is given is the author's choice. -->
			<div class="bg-muted/40 space-y-2 rounded-md border p-2.5">
				<div class="space-y-1">
					<span class="text-sm font-medium">Always given</span>
					<p class="text-muted-foreground text-xs leading-snug">
						Your firm, who acted, and when. Merge fields lists the email's own tokens.
					</p>
				</div>
				<div class="space-y-1 border-t pt-2">
					<span class="text-sm font-medium">Also about</span>
					<p class="text-muted-foreground text-xs leading-snug">
						Ticking one adds its details below.
					</p>
					<div class="flex flex-wrap gap-x-4 gap-y-1.5 pt-0.5">
						{#each SUBJECT_ORDER as kind (kind)}
							<label class="flex items-center gap-1.5 text-sm">
								<Checkbox
									checked={pending.includes(kind)}
									disabled={!onsubjects}
									onCheckedChange={() => toggleSubject(kind)}
								/>
								{SUBJECT_LABELS[kind]}
							</label>
						{/each}
					</div>
					{#if changed}
						<p class="text-warning-strong pt-0.5 text-xs leading-snug">Not saved yet.</p>
					{/if}
				</div>
			</div>

			<!-- THE OTHER HALF OF THE CHECKBOXES. Listing them is what makes ticking a box visibly do
			     something. NOT tokens to type into the canvas: the send passes the email flat keys
			     (`account_name`), while these dotted paths are what a notification RULE's own title and
			     message resolve against — so the copy below says where they are readable rather than
			     inviting an author to type one here and get a blank. -->
			{#if scopeGroups.length}
				<div>
					<SectionHeading title="Details these contexts carry" class="mb-1" />
					<p class="text-muted-foreground mb-1.5 text-xs leading-snug">
						A notification rule's own title and message can use these — write
						<code>&lbrace;account.value&rbrace;</code> there and the send fills it in.
					</p>
					<div class="divide-border divide-y rounded-md border">
						{#each scopeGroups as group (group.group)}
							<div>
								<p
									class="text-muted-foreground bg-muted/40 px-2.5 py-1 text-[0.6rem] font-semibold tracking-wide uppercase"
								>
									{group.group}
								</p>
								{#each group.fields as field (field.path)}
									<div class="flex items-baseline gap-3 px-2.5 py-1">
										<span class="min-w-0 flex-1 truncate text-xs">{field.label}</span>
										<code class="text-muted-foreground shrink-0 text-[0.7rem]">{field.path}</code>
									</div>
								{/each}
							</div>
						{/each}
					</div>
				</div>
			{/if}
		</div>

		<!-- The two rules an author cannot see from a list of names, ONE LINE EACH: at a paragraph apiece
		     they were a wall nobody read, which is worse than not stating them. -->
		<div class="text-muted-foreground space-y-1 text-xs leading-snug">
			<p>
				An empty field prints <strong class="text-foreground font-medium">nothing</strong> — set
				<Badge variant="outline" class="px-1 py-0 font-mono text-[0.65rem]">If empty</Badge>
				on it for words to show instead.
			</p>
			<p>
				A list is never printed — put
				<Badge variant="outline" class="px-1 py-0 text-[0.65rem]">Repeat</Badge>
				on a row and write <code>&lbrace;&lbrace; item &rbrace;&rbrace;</code> in it.
			</p>
		</div>

		<Dialog.Footer>
			{#if onsubjects}
				<!-- A SETTING IS SAVED ON PURPOSE: ticking a box shows the consequence, this writes it. -->
				<Button type="button" variant="ghost" onclick={() => (open = false)}>Cancel</Button>
				<Button type="button" disabled={!changed} onclick={save}>Save context</Button>
			{:else}
				<Button type="button" onclick={() => (open = false)}>Close</Button>
			{/if}
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
