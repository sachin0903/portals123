<script lang="ts">
	// Shared mount of the client record for Overview and Details. The pages name the band and
	// the title; this owns the wiring that both need (links, actions).
	// Details posts mutations to the overview page (an absolute `/clients/{uid}?/…`) because
	// the form actions live there — one implementation, two doors. A relative `../?/` pops
	// both `details` and `{uid}` on this route.
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import {
		ClientRecord,
		ConfirmDialog,
		Label,
		Textarea,
		clientName,
		needsAction,
		toast,
		type ClientJourneySummary,
		type ClientRecordView,
		type Transaction
	} from '@graphene/ui';
	import type { ComponentProps } from 'svelte';
	import ActionRequired from '$lib/components/transactions/action-required.svelte';
	import type { ContactDetail } from '$lib/api';

	type RecordProps = ComponentProps<typeof ClientRecord>;

	let {
		data,
		section,
		actionsPrefix = '?/'
	}: {
		data: {
			detail: ContactDetail;
			allocations?: RecordProps['allocations'];
			cash?: number;
			bookCost?: number | null;
			unrealisedGain?: number | null;
			holdingsError?: boolean;
			instructions?: RecordProps['instructions'];
			funding?: RecordProps['funding'];
			bankAccount?: RecordProps['bankAccount'];
			pendingReviews?: RecordProps['pendingReviews'];
			justificationRequired?: RecordProps['justificationRequired'];
			pendingTransactions?: Transaction[];
			canManage: boolean;
			canInvitePortal?: boolean;
			canMoveMoney: boolean;
			canIllustrate: boolean;
			canViewAs?: boolean;
			journeys?: ClientJourneySummary[];
			newJourneys?: { code: string; name: string; href: string }[];
			classifications?: { uid: string; name: string; slug: string }[];
			groups?: { uid: string; name: string; slug: string }[];
			assignedGroups?: { uid: string; name: string; slug: string }[];
			resolvedClassification?: {
				uid: string;
				name: string;
				slug: string;
				source: 'pinned' | 'matched' | 'default';
			} | null;
		};
		section: ClientRecordView;
		actionsPrefix?: string;
	} = $props();

	const pensionLabel = $derived(
		data.detail.accounts.filter((a) => a.is_pension)[0]?.product_type?.toUpperCase() ?? 'Pension'
	);
	const outstanding = $derived(needsAction(data.pendingTransactions ?? []));
	const act = (name: string) => `${actionsPrefix}${name}`;

	// ---- viewing this client's own portal ------------------------------------------------
	// A separate tab showing what the client sees, opened as them and recorded against us. The
	// reason is asked for here because it is written onto the session and read back off the
	// trail; a session nobody can account for afterwards is worse than no session.
	const name = $derived(clientName(data.detail.contact));
	let viewAsOpen = $state(false);
	let viewAsReason = $state('');
	let viewAsBusy = $state(false);

	async function openClientPortal(): Promise<void> {
		const reason = viewAsReason.trim();
		if (reason.length < 3 || viewAsBusy) return;
		viewAsBusy = true;
		// The tab is opened on the click that started this, before the await. A browser blocks a
		// window opened out of an async continuation, so the alternative is a blocked popup on
		// every attempt.
		const tab = window.open('', '_blank');
		try {
			const res = await fetch(`/clients/${data.detail.contact.uid}/view-as`, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ reason })
			});
			const body = (await res.json()) as { url?: string; error?: string };
			if (!res.ok || !body.url) {
				tab?.close();
				toast.error(body.error ?? 'That client portal could not be opened.');
				return;
			}
			if (tab) tab.location.href = body.url;
			else window.location.assign(body.url);
			viewAsOpen = false;
			viewAsReason = '';
		} catch {
			tab?.close();
			toast.error('That client portal could not be opened.');
		} finally {
			viewAsBusy = false;
		}
	}
</script>

<ClientRecord
	detail={data.detail}
	screeningChecks={data.detail.kyc}
	allocations={data.allocations ?? []}
	cash={data.cash ?? 0}
	bookCost={data.bookCost ?? null}
	unrealisedGain={data.unrealisedGain ?? null}
	holdingsError={data.holdingsError ?? false}
	instructions={data.instructions ?? []}
	funding={data.funding ?? []}
	bankAccount={data.bankAccount ?? null}
	pendingReviews={data.pendingReviews ?? []}
	justificationRequired={data.justificationRequired ?? []}
	{pensionLabel}
	pickers={{
		members: data.canManage ? '/pickers/members' : undefined,
		countries: '/pickers/countries'
	}}
	{section}
	classifications={data.classifications ?? []}
	resolvedClassification={data.resolvedClassification ?? null}
	groups={data.groups ?? []}
	assignedGroups={data.assignedGroups ?? []}
	links={{
		account: (uid) => `/accounts/${uid}`,
		client: (uid) => `/clients/${uid}`,
		review: (uid) => `/reviews?open=${uid}`,
		document: (uid) => `/documents/${uid}`,
		illustrations: null,
		tasks: null,
		journey: (uid) => `/clients/${data.detail.contact.uid}/journeys/${uid}`,
		newJourneys: data.newJourneys ?? []
	}}
	journeys={data.journeys ?? []}
	actions={data.canManage || data.canMoveMoney || data.canViewAs
		? {
				enhance,
				...(data.canViewAs ? { onviewas: () => (viewAsOpen = true) } : {}),
				...(data.canManage
					? {
							updateClient: act('updateClient'),
							setVulnerability: act('setVulnerability'),
							closeClient: act('closeClient'),
							restoreClient: act('restoreClient'),
							addNote: act('addNote'),
							assignAdviser: act('assignAdviser'),
							unassignAdviser: act('unassignAdviser'),
							linkUser: act('linkUser'),
							unlinkUser: act('unlinkUser'),
							deleteNote: act('deleteNote'),
							pinClassification: act('pinClassification'),
							setGroups: act('setGroups'),
							...(data.canInvitePortal ? { invitePortal: act('invitePortal') } : {})
						}
					: {}),
				...(data.canMoveMoney
					? {
							updateBankAccount: act('updateBankAccount'),
							setAccountModel: act('setAccountModel'),
							createInstruction: act('createInstruction'),
							cancelInstruction: act('cancelInstruction'),
							cancelReview: act('cancelReview')
						}
					: {})
			}
		: null}
	onretry={() => invalidateAll()}
>
	{#snippet queue()}
		<ActionRequired items={outstanding} showClient={false} />
	{/snippet}
</ClientRecord>

<!-- Opening this client's own portal. The reason is required: it is written onto the session and
     is what the trail shows beside who looked and for how long. -->
<ConfirmDialog
	bind:open={viewAsOpen}
	title="View {name}’s portal?"
	description="A new tab opens showing their client portal exactly as they see it. It is read-only — you cannot act for them from inside it — and the session lasts an hour. Who looked, at whom, and why is recorded against your name."
	confirmLabel="Open their portal"
	confirmDisabled={viewAsReason.trim().length < 3 || viewAsBusy}
	onconfirm={() => void openClientPortal()}
>
	<div class="space-y-2">
		<Label for="view-as-reason">Why are you opening it?</Label>
		<Textarea
			id="view-as-reason"
			bind:value={viewAsReason}
			rows={3}
			placeholder="e.g. Walking them through their documents on the telephone"
		/>
	</div>
</ConfirmDialog>
