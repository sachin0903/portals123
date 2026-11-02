<script lang="ts">
	// The "ask a client for something" builder — a client, a deadline, a covering note and one or
	// more independently-fulfilled lines (upload / agree / acknowledge / complete a form). Extracted
	// from the Requests page so a client's Documents tab can raise a request IN PLACE (locked to that
	// client) instead of navigating away. The caller owns the server action + what happens on success.
	import { Button, Dialog, Input, Label, SelectMenu, Textarea, toast } from '@graphene/ui';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import UserIcon from '@lucide/svelte/icons/user';
	import UsersIcon from '@lucide/svelte/icons/users';
	import {
		REQUEST_ACTION_HINTS,
		REQUEST_ACTION_LABELS,
		type ApiDocument,
		type ClientOption,
		type RequestAction
	} from './documents.js';
	import type { Enhance } from '../clients/form-enhance.js';

	let {
		open = $bindable(false),
		action,
		clients = [],
		documents = [],
		forms = [],
		forClient = null,
		forGroup = null,
		enhance,
		invalidate,
		onCreated
	}: {
		open?: boolean;
		/** The form action this dialog posts to (e.g. `?/create` on Requests, `?/createRequest` on a
		    client's tab, `?/createGroupRequest` for a group). Locked-target actions (`forClient`/
		    `forGroup`) need only `priority`, `due_date`, `message` and a JSON `items` field; the
		    unlocked (picker) case also needs `contact_id`. Returns `{ uid }` on success for a single
		    client, or anything truthy for a group (its own uids aren't surfaced here). */
		action: string;
		/** Clients for the picker. Ignored (and the picker hidden) when `forClient`/`forGroup` locks
		    the target. */
		clients?: ClientOption[];
		/** Live documents an agree/acknowledge line can point at. */
		documents?: ApiDocument[];
		/** Published forms a "complete a form" line can reference. */
		forms?: { uid: string; name: string }[];
		/** Opened from a client's page: the request is locked to this client (no picker, a reminder). */
		forClient?: ClientOption | null;
		/** Opened from a group's chat: the SAME request is raised against every member (no picker, a
		    reminder) — one independent request per member, same as the fan-out send. Mutually
		    exclusive with `forClient`. */
		forGroup?: { id: number; uid?: string; name: string } | null;
		// SvelteKit virtuals exist only in the host app; inject them across the package boundary.
		enhance: Enhance;
		invalidate?: () => Promise<void>;
		/** Called after a successful create, with the new request's uid (single-client) or undefined
		    (group — there's no single uid to report). The dialog has already toasted, closed and
		    invalidated; use this to navigate (Requests page) or do nothing (client tab/group chat). */
		onCreated?: (uid: string | undefined) => void | Promise<void>;
	} = $props();

	type DraftItem = {
		key: number;
		action: RequestAction;
		title: string;
		targetDocumentUid: string;
		formUid: string;
	};
	let creating = $state(false);
	let contactId = $state('');
	let priority = $state('normal');
	let items = $state<DraftItem[]>([]);
	let nextKey = 0;

	function blankItem(): DraftItem {
		nextKey += 1;
		return { key: nextKey, action: 'upload', title: '', targetDocumentUid: '', formUid: '' };
	}

	// Reset the draft every time the dialog opens (seeding the locked client), so a cancelled draft
	// never survives into the next open.
	$effect(() => {
		if (open) {
			contactId = forClient ? String(forClient.contact_id) : '';
			priority = 'normal';
			items = [blankItem()];
		}
	});

	function clientName(c: ClientOption): string {
		return c.name ?? c.email ?? `Client #${c.contact_id}`;
	}

	const clientItems = $derived(
		clients.map((c) => ({ value: String(c.contact_id), label: clientName(c) }))
	);
	// Only real, live documents can be the target of an agree/acknowledge.
	const documentItems = $derived(
		documents
			.filter((d) => d.status === 'generated')
			.map((d) => ({ value: d.uid, label: d.name ?? 'Untitled document' }))
	);
	const formItems = $derived(forms.map((f) => ({ value: f.uid, label: f.name })));
	const actionItems = (Object.keys(REQUEST_ACTION_LABELS) as RequestAction[]).map((a) => ({
		value: a,
		label: REQUEST_ACTION_LABELS[a]
	}));

	/** The payload the action parses — kept in a hidden field so the whole builder posts in one go. */
	const itemsPayload = $derived(
		JSON.stringify(
			items.map((i) => ({
				action: i.action,
				title: i.title.trim(),
				target_document_uid:
					i.action === 'agree' || i.action === 'acknowledge' ? i.targetDocumentUid || null : null,
				form_uid: i.action === 'complete_form' ? i.formUid || null : null
			}))
		)
	);

	/** Every line complete enough for the API to accept it (mirrors create_request's guards). */
	const draftValid = $derived(
		(!!contactId || !!forGroup) &&
			items.length > 0 &&
			items.every(
				(i) =>
					i.title.trim().length > 0 &&
					(i.action === 'agree' || i.action === 'acknowledge' ? !!i.targetDocumentUid : true) &&
					(i.action === 'complete_form' ? !!i.formUid : true)
			)
	);
	// An agree/acknowledge item is unusable without a document to point at, and complete_form without
	// a published form — say so instead of letting the dialog fail on submit.
	const missingTargets = $derived(
		items.some((i) => (i.action === 'agree' || i.action === 'acknowledge') && !documentItems.length)
	);
	const missingForms = $derived(
		items.some((i) => i.action === 'complete_form' && !formItems.length)
	);
</script>

<!-- Ask a client for something: a client, a deadline, a covering note and one or more lines. -->
<Dialog.Root bind:open>
	<Dialog.Content class="flex max-h-[90svh] flex-col sm:max-w-2xl">
		<Dialog.Header>
			<Dialog.Title>Ask a client for something</Dialog.Title>
			<Dialog.Description>
				Each line is fulfilled on its own, so one request can cover several asks.
			</Dialog.Description>
		</Dialog.Header>
		<!-- The FORM is the flex column; the FIELDS scroll inside it and `Dialog.Footer` stays put.
		     The footer was inside the scrolling element, and it carries `-mx-4 -mb-4` to break out of
		     `Dialog.Content`'s `p-4` so its bar reaches the dialog's edges and its `rounded-b-xl`
		     meets the corners. Inside a scroll container those negative margins make it 2rem wider
		     than the content box, which overflows — and `overflow-y-auto` computes `overflow-x` from
		     `visible` to `auto`, so the dialog grew a horizontal scrollbar. Out here the form's box is
		     already inset by Content's padding, so the breakout resolves against the dialog exactly as
		     it does for every other Dialog.Footer in the app; the footer also stops scrolling away
		     from a long draft, which is the point of a `max-h` dialog. `overflow-x-hidden` would have
		     hidden the bar by clipping the bar the footer is trying to draw. -->
		<form
			method="POST"
			{action}
			class="flex min-h-0 flex-1 flex-col gap-4"
			use:enhance={() => {
				creating = true;
				return async ({ result, update }) => {
					creating = false;
					if (result.type === 'success') {
						open = false;
						toast.success(
							forGroup
								? `Request sent to every member of ${forGroup.name}.`
								: 'Request sent to the client.'
						);
						await invalidate?.();
						await onCreated?.(typeof result.data?.uid === 'string' ? result.data.uid : undefined);
					} else {
						toast.error(
							result.type === 'failure'
								? String(result.data?.error ?? 'Something went wrong.')
								: 'Something went wrong.'
						);
						await update({ reset: false });
					}
				};
			}}
		>
			<input type="hidden" name="items" value={itemsPayload} />
			<!-- `px-1 -mx-1` so a focus ring on an edge control is not clipped by the scroll box. -->
			<div class="-mx-1 min-h-0 flex-1 space-y-4 overflow-y-auto px-1">
				<!-- Locked to a client or a group (opened from their page/chat): a reminder + a hidden
			     field, no picker. -->
				{#if forClient || forGroup}
					{#if forClient}
						<input type="hidden" name="contact_id" value={forClient.contact_id} />
					{:else if forGroup}
						<input type="hidden" name="group_id" value={forGroup.id} />
						{#if forGroup.uid}
							<input type="hidden" name="group_uid" value={forGroup.uid} />
						{/if}
					{/if}
					<div
						class="bg-muted/50 text-muted-foreground flex items-center gap-2 rounded-lg border px-3 py-2 text-sm"
					>
						{#if forGroup}
							<UsersIcon class="size-4 shrink-0" />
							<span
								>Requesting from every member of <span class="text-foreground font-medium"
									>{forGroup.name}</span
								></span
							>
						{:else if forClient}
							<UserIcon class="size-4 shrink-0" />
							<span
								>Requesting from <span class="text-foreground font-medium"
									>{clientName(forClient)}</span
								></span
							>
						{/if}
					</div>
					<div class="space-y-1.5">
						<Label for="req-priority">Priority</Label>
						<SelectMenu
							id="req-priority"
							name="priority"
							items={[
								{ value: 'low', label: 'Low' },
								{ value: 'normal', label: 'Normal' },
								{ value: 'high', label: 'High' },
								{ value: 'urgent', label: 'Urgent' }
							]}
							bind:value={priority}
						/>
					</div>
				{:else}
					<div class="grid gap-3 sm:grid-cols-2">
						<div class="space-y-1.5">
							<Label for="req-client">Client</Label>
							<SelectMenu
								id="req-client"
								name="contact_id"
								required
								placeholder={clientItems.length ? 'Choose a client…' : 'No clients yet'}
								items={clientItems}
								bind:value={contactId}
							/>
						</div>
						<div class="space-y-1.5">
							<Label for="req-priority">Priority</Label>
							<SelectMenu
								id="req-priority"
								name="priority"
								items={[
									{ value: 'low', label: 'Low' },
									{ value: 'normal', label: 'Normal' },
									{ value: 'high', label: 'High' },
									{ value: 'urgent', label: 'Urgent' }
								]}
								bind:value={priority}
							/>
						</div>
					</div>
				{/if}
				<div class="space-y-1.5">
					<Label for="req-due">Due date <span class="text-muted-foreground">(optional)</span></Label
					>
					<Input id="req-due" name="due_date" type="date" />
				</div>
				<div class="space-y-1.5">
					<Label for="req-msg">
						Covering note <span class="text-muted-foreground">(optional)</span>
					</Label>
					<Textarea id="req-msg" name="message" rows={2} placeholder="Why you need this…" />
				</div>

				<div class="space-y-2">
					<div class="flex items-center justify-between">
						<Label>What you are asking for</Label>
						<Button
							type="button"
							variant="ghost"
							size="sm"
							onclick={() => (items = [...items, blankItem()])}
						>
							<PlusIcon class="size-3.5" /> Add another
						</Button>
					</div>
					{#each items as item (item.key)}
						<div class="space-y-2 rounded-lg border p-3">
							<div class="flex items-start gap-2">
								<div class="min-w-0 flex-1 space-y-2">
									<div class="grid gap-2 sm:grid-cols-[11rem_1fr]">
										<div>
											<Label for={`item-action-${item.key}`} class="sr-only"
												>What the client does</Label
											>
											<SelectMenu
												id={`item-action-${item.key}`}
												items={actionItems}
												value={item.action}
												onValueChange={(v) => (item.action = v as RequestAction)}
											/>
										</div>
										<div>
											<Label for={`item-title-${item.key}`} class="sr-only">What to ask for</Label>
											<Input
												id={`item-title-${item.key}`}
												bind:value={item.title}
												placeholder="e.g. Your latest pension statement"
											/>
										</div>
									</div>
									{#if item.action === 'agree' || item.action === 'acknowledge'}
										<Label for={`item-target-${item.key}`} class="sr-only">Document to act on</Label
										>
										<SelectMenu
											id={`item-target-${item.key}`}
											items={documentItems}
											placeholder={documentItems.length
												? 'Document to act on…'
												: 'No documents available'}
											bind:value={item.targetDocumentUid}
										/>
									{:else if item.action === 'complete_form'}
										<Label for={`item-form-${item.key}`} class="sr-only">Form to complete</Label>
										<SelectMenu
											id={`item-form-${item.key}`}
											items={formItems}
											placeholder={formItems.length ? 'Form to complete…' : 'No published forms'}
											bind:value={item.formUid}
										/>
									{/if}
									<p class="text-muted-foreground text-xs">{REQUEST_ACTION_HINTS[item.action]}</p>
								</div>
								{#if items.length > 1}
									<Button
										type="button"
										variant="ghost"
										size="icon"
										class="text-muted-foreground hover:text-destructive shrink-0"
										aria-label="Remove this line"
										onclick={() => (items = items.filter((i) => i.key !== item.key))}
									>
										<Trash2Icon class="size-4" />
									</Button>
								{/if}
							</div>
						</div>
					{/each}
					{#if missingTargets}
						<p class="text-destructive text-xs" role="alert">
							An agree or acknowledge line needs a document to point at, and this firm has none yet.
						</p>
					{/if}
					{#if missingForms}
						<p class="text-destructive text-xs" role="alert">
							A form line needs a published form — publish one under Forms first.
						</p>
					{/if}
				</div>
			</div>

			<Dialog.Footer>
				<Button type="button" variant="outline" onclick={() => (open = false)}>Cancel</Button>
				<Button type="submit" disabled={creating || !draftValid}>
					{creating ? 'Sending…' : 'Send request'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
