<script lang="ts">
	/**
	 * A personal checklist for the dashboard rail — the notes an adviser keeps beside the book.
	 *
	 * Stored centrally, per user (`platform.staff_notes`; see `./notes.ts`), so the list follows its
	 * owner to the next machine. The portal loads it with the dashboard and this component writes
	 * through the portal's proxy under `endpoint`. Every edit is optimistic — the list moves at
	 * once, and a failed save puts it back and says so — because a note is meant to be as cheap to
	 * write as a thought.
	 */
	import { SvelteSet } from 'svelte/reactivity';
	import CheckIcon from '@lucide/svelte/icons/check';
	import XIcon from '@lucide/svelte/icons/x';
	import { notesClient, type NotesClient, type StaffNote } from './notes.js';

	let {
		notes,
		endpoint = '/me/notes',
		client
	}: {
		/** The owner's list as the portal loaded it. Replaced wholesale when the loader re-runs. */
		notes: StaffNote[];
		/** The portal's proxy for `/me/notes` — relative, so no token lives here. */
		endpoint?: string;
		/** Stand-in for the network; tests inject one. */
		client?: NotesClient;
	} = $props();

	const api = $derived(client ?? notesClient(endpoint));

	let items = $state<StaffNote[]>([]);
	$effect(() => {
		// The server copy is the truth; a re-run of the loader replaces what is shown.
		items = notes.map((n) => ({ ...n }));
	});

	let draft = $state('');
	/** Bumped to remount the input empty — clearing a bound value does not, once it has been typed. */
	let draftKey = $state(0);
	/** Notes whose text is being edited. A done note is not editable: ticking it was the decision. */
	const editing = new SvelteSet<string>();
	/** The last failed save, until the next one lands. */
	let failure = $state<string | null>(null);

	let tmp = 0;

	async function add(): Promise<void> {
		const body = draft.trim();
		if (!body) return;
		draft = '';
		draftKey += 1;
		// Shown at once under a placeholder uid; the server's row replaces it.
		const placeholder: StaffNote = { uid: `pending-${++tmp}`, body, done: false, position: 0 };
		items.push(placeholder);
		try {
			const saved = await api.add(body);
			const i = items.findIndex((n) => n.uid === placeholder.uid);
			if (i >= 0) items[i] = saved;
			failure = null;
		} catch {
			items = items.filter((n) => n.uid !== placeholder.uid);
			failure = 'Couldn’t save that note.';
		}
	}

	async function toggle(note: StaffNote): Promise<void> {
		editing.delete(note.uid);
		const was = note.done;
		note.done = !was;
		try {
			await api.edit(note.uid, { done: note.done });
			failure = null;
		} catch {
			note.done = was;
			failure = 'Couldn’t save that note.';
		}
	}

	async function commit(note: StaffNote, value: string): Promise<void> {
		editing.delete(note.uid);
		const body = value.trim();
		if (!body) return remove(note);
		if (body === note.body) return;
		const was = note.body;
		note.body = body;
		try {
			await api.edit(note.uid, { body });
			failure = null;
		} catch {
			note.body = was;
			failure = 'Couldn’t save that note.';
		}
	}

	async function remove(note: StaffNote): Promise<void> {
		const at = items.indexOf(note);
		items = items.filter((n) => n.uid !== note.uid);
		try {
			await api.remove(note.uid);
			failure = null;
		} catch {
			items.splice(at < 0 ? items.length : at, 0, note);
			failure = 'Couldn’t remove that note.';
		}
	}
</script>

<div class="flex flex-col gap-1.5">
	<div class="flex items-baseline justify-between gap-2">
		<h3 class="text-sm font-medium">My notes</h3>
		{#if failure}
			<span class="text-destructive text-xs" role="status">{failure}</span>
		{/if}
	</div>
	<ul class="flex flex-col">
		{#each items as note (note.uid)}
			<li class="group/note -mx-1.5 flex items-center gap-2 rounded-md px-1.5 py-1">
				<button
					type="button"
					onclick={() => toggle(note)}
					aria-pressed={note.done}
					aria-label={note.done ? 'Mark not done' : 'Mark done'}
					class="flex size-3.5 shrink-0 items-center justify-center rounded-sm border {note.done
						? 'border-brand bg-brand text-brand-foreground'
						: 'border-input'}"
				>
					{#if note.done}<CheckIcon class="size-2.5" />{/if}
				</button>
				{#if editing.has(note.uid)}
					<input
						class="min-w-0 flex-1 bg-transparent text-xs outline-none"
						value={note.body}
						onblur={(e) => commit(note, e.currentTarget.value)}
						onkeydown={(e) => {
							if (e.key === 'Enter') e.currentTarget.blur();
							if (e.key === 'Escape') {
								e.currentTarget.value = note.body;
								e.currentTarget.blur();
							}
						}}
					/>
				{:else}
					<button
						type="button"
						class="min-w-0 flex-1 truncate text-left text-xs {note.done
							? 'text-muted-foreground line-through'
							: ''}"
						onclick={() => {
							if (!note.done) editing.add(note.uid);
						}}
					>
						{note.body}
					</button>
				{/if}
				<button
					type="button"
					onclick={() => remove(note)}
					aria-label="Remove note"
					class="text-muted-foreground hover:text-foreground shrink-0 opacity-0 transition-opacity group-hover/note:opacity-100 focus-visible:opacity-100"
				>
					<XIcon class="size-3" />
				</button>
			</li>
		{/each}
	</ul>
	<form
		class="flex items-center gap-2 px-0 py-1"
		onsubmit={(e) => {
			e.preventDefault();
			void add();
		}}
	>
		<span class="border-input size-3.5 shrink-0 rounded-sm border border-dashed" aria-hidden="true"
		></span>
		{#key draftKey}
			<input
				bind:value={draft}
				placeholder="Add a note"
				aria-label="Add a note"
				maxlength="500"
				class="placeholder:text-muted-foreground min-w-0 flex-1 bg-transparent text-xs outline-none"
			/>
		{/key}
	</form>
</div>
