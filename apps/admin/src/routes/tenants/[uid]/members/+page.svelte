<script lang="ts">
	import { untrack } from 'svelte';
	import { invalidateAll } from '$app/navigation';
	import { enhance } from '$app/forms';
	import {
		Button,
		Card,
		CardContent,
		ConfirmDialog,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		EmptyState,
		humanise,
		Input,
		EmailInput,
		Label,
		LoadErrorBanner,
		PageHeader,
		SectionHeading,
		SelectMenu,
		sentenceCase,
		StatusBadge,
		type StatusTone,
		toast
	} from '@graphene/ui';
	import { ListingToolbar, matches, search, type FilterDef } from '@graphene/ui/book/listing';
	import UsersIcon from '@lucide/svelte/icons/users';
	import type { SubmitFunction } from '@sveltejs/kit';
	import type { PageData, ActionData } from './$types';
	import type { Member } from '$lib/api';
	// Derived member state (held vs revoked portal, invited vs suspended) lives in $lib/members with
	// its own tests -- it is the part that was getting the labels wrong.
	import {
		accountState,
		anyPending,
		pendingPortal,
		portalOf,
		revokedPortal,
		syncMessage
	} from '$lib/members';
	import InvitationReveal from '$lib/InvitationReveal.svelte';
	import { startReveal, type Reveal } from '$lib/invitation';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	// A set-password link only ever reaches this page after the operator asks for one ("Copy invite
	// link"), never as a by-product of inviting or resending — it sets somebody's password, so it
	// must not ride along in responses that end up in proxy logs and browser history. Once here it
	// is held in local state, NOT read from `form` at render time, so it can be dropped (on copy,
	// on discard, on its TTL) even though the action result still carries it.
	let reveal = $state<Reveal | null>(null);
	let revealed: unknown = undefined; // the form result already consumed (identity, not value)
	$effect(() => {
		const result = form;
		if (result === revealed) return;
		revealed = result;
		if (result?.action === 'invitationLink' && 'invitationUrl' in result && result.invitationUrl) {
			const url = String(result.invitationUrl);
			const userId = 'userId' in result ? (result.userId ?? null) : null;
			untrack(() => (reveal = startReveal(url, userId)));
		}
	});
	const revealWho = $derived(
		reveal ? (data.members.find((m) => m.user_id === reveal?.userId)?.email ?? null) : null
	);

	// `data.members` is this firm's whole roster from one unpaged call — unlike /tenants above,
	// there is no fetched-page-vs-estate gap here, so filtering it in the browser is the real
	// answer, not an approximation of one. `accountState` (from $lib/members) is reused rather
	// than restated here, because it's the piece that was getting "invited" vs "suspended" wrong.
	let query = $state('');
	// MULTI: "Active and Invited" — everyone but the suspended — is a real question here.
	let statusFilter = $state<string[]>([]);
	const statusOptions = [
		{ value: 'active', label: 'Active' },
		{ value: 'invited', label: 'Invited' },
		{ value: 'suspended', label: 'Suspended' }
	];
	const filterDefs = $derived<FilterDef[]>([
		{ key: 'status', label: 'Status', options: statusOptions }
	]);
	const filterValue = $derived<Record<string, string[]>>(
		statusFilter.length ? { status: statusFilter } : {}
	);
	function onMemberFilters(next: Record<string, string[]>) {
		statusFilter = next.status ?? [];
	}
	const dirty = $derived(statusFilter.length > 0);
	function clearFilters(): void {
		statusFilter = [];
	}
	const filteredMembers = $derived(
		data.members.filter(
			(m) => matches(statusFilter, accountState(m)) && search(query, m.name, m.email)
		)
	);

	// The two portals managed here — operations is managed elsewhere.
	const PORTALS = [
		{ key: 'client', label: 'Client' },
		{ key: 'platform', label: 'Platform' }
	] as const;

	// Only Person names a value worth alphabetising. Each portal's cell is a grant/revoke action,
	// not a fact to compare, and Account is nothing but actions — so neither takes `sortable`.
	const memberColumns: DataTableColumn<Member>[] = [
		{
			key: 'name',
			header: 'Person',
			sortable: true,
			sortDefault: true,
			cellClass: 'align-top',
			sortValue: (m) => m.name ?? m.email
		},
		...PORTALS.map((p): DataTableColumn<Member> => ({
			key: p.key,
			header: `${p.label} access`,
			cellClass: 'align-top'
		})),
		{ key: 'account', header: 'Account', align: 'right', cellClass: 'align-top' }
	];
	let memberSort = $state<DataTableSort | null>(null);

	const rolesFor = (portal: string) => data.roleOptions.filter((r) => r.portal === portal);
	const roleItems = (portal: string) =>
		rolesFor(portal).map((r) => ({ value: r.slug, label: r.name }));
	const inviteRoleItems = $derived(
		data.roleOptions.map((r) => ({ value: r.slug, label: `${r.name} (${r.portal ?? '—'})` }))
	);
	// Native selects defaulted to their first option; keep that behaviour.
	let inviteRole = $state(untrack(() => data.roleOptions[0]?.slug ?? ''));

	const STATUS_TONE: Record<string, StatusTone> = { active: 'success', pending: 'warning' };
	const STATUS_LABEL: Record<string, string> = {
		active: 'Active',
		pending: 'Invited',
		inactive: 'Revoked'
	};

	/**
	 * What reactivation actually did. It restores the access recorded when the person was suspended,
	 * so a portal revoked beforehand stays revoked — the operator has to be told that, or they will
	 * read "reactivated" as "everything back". Where no snapshot exists (suspended before it was
	 * recorded) the restore IS blanket, and the message says so rather than claiming precision.
	 */
	function reactivatedMessage(d: Record<string, unknown> | undefined): string {
		const kept = (d?.portalsLeftRevoked ?? []) as string[];
		const back = (d?.portalsRestored ?? []) as string[];
		const names = (list: string[]) => list.map(sentenceCase).join(' and ');
		if (kept.length > 0)
			return `Member reactivated: ${names(back) || 'no portal'} restored, ${names(kept)} left revoked.`;
		if (d?.snapshotFound) return `Member reactivated. Restored: ${names(back) || 'no portal'}.`;
		return 'Member reactivated with access to every portal they hold. Revoke any access they should not have.';
	}

	// Per-form pending flags + a shared enhance callback that reports outcomes via toast.
	let pending = $state<Record<string, boolean>>({});
	function submitWith(
		key: string,
		success: string | ((data: Record<string, unknown> | undefined) => string),
		opts: { reset?: boolean } = {}
	): SubmitFunction {
		return () => {
			pending[key] = true;
			return async ({ result, update }) => {
				pending[key] = false;
				if (result.type === 'success') {
					toast.success(typeof success === 'function' ? success(result.data) : success);
					await update({ reset: opts.reset ?? false });
				} else {
					toast.error(
						result.type === 'failure'
							? String(result.data?.error ?? 'Something went wrong.')
							: 'Something went wrong.'
					);
					await update();
				}
			};
		};
	}

	// Destructive row actions confirm first; requestSubmit keeps the form-action wiring.
	let rowForms: Record<string, HTMLFormElement | undefined> = {};
	let deactivateOpen = $state(false);
	let deactivateTarget = $state<Member | null>(null);
	let revokeOpen = $state(false);
	let revokeTarget = $state<{ member: Member; portal: string; label: string } | null>(null);
	let mfaOpen = $state(false);
	let mfaTarget = $state<Member | null>(null);
</script>

<PageHeader
	eyebrow={data.tenant.name}
	title="Members"
	description="The people who belong to this firm."
>
	{#snippet actions()}
		{#if data.canManage}
			<form method="POST" action="?/sync" use:enhance={submitWith('sync', syncMessage)}>
				<Button type="submit" variant="secondary" size="sm" disabled={pending.sync}>
					{pending.sync ? 'Syncing…' : 'Sync from WorkOS'}
				</Button>
			</form>
		{/if}
	{/snippet}
</PageHeader>

<div class="space-y-6">
	{#if data.apiError}
		<LoadErrorBanner message={data.apiError} onRetry={() => invalidateAll()} />
	{/if}

	<!-- Invite a person -->
	<section>
		<SectionHeading title="Invite a member" />
		<Card glass>
			<CardContent class="space-y-3">
				<p class="text-muted-foreground text-sm">
					Emails them a link to set a password on this firm's own portal. The role you pick sets
					their first portal; you can grant the other one afterwards. If the email doesn't arrive,
					use "Copy invite link" on their row.
				</p>
				{#if data.roleOptions.length === 0}
					<p class="text-destructive text-sm">
						This firm has no roles yet, so nobody can be invited.
						<a href="/tenants/{data.tenant.uid}/roles" class="underline">Configure its roles</a> first.
					</p>
				{/if}
				<form
					method="POST"
					action="?/addMember"
					use:enhance={submitWith(
						'invite',
						(d) =>
							d?.emailDelivered
								? `Invitation emailed to ${d?.email}.`
								: 'Member created, but the invitation email was not sent. Use "Copy invite link" on their row.',
						{ reset: true }
					)}
					class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
				>
					<div class="space-y-1">
						<Label for="first_name">First name</Label>
						<Input id="first_name" name="first_name" disabled={!data.canManage} />
					</div>
					<div class="space-y-1">
						<Label for="last_name">Last name</Label>
						<Input id="last_name" name="last_name" disabled={!data.canManage} />
					</div>
					<div class="space-y-1">
						<Label for="email">Email</Label>
						<EmailInput
							id="email"
							name="email"
							placeholder="person@firm.com"
							disabled={!data.canManage}
						/>
					</div>
					<div class="space-y-1">
						<Label for="role_slug">Role</Label>
						<SelectMenu
							id="role_slug"
							name="role_slug"
							items={inviteRoleItems}
							bind:value={inviteRole}
							placeholder="Choose a role…"
							disabled={!data.canManage}
						/>
					</div>
					{#if data.canManage}
						<div class="sm:col-span-2 lg:col-span-4">
							<Button type="submit" disabled={pending.invite}>
								{pending.invite ? 'Sending…' : 'Send invitation'}
							</Button>
						</div>
					{/if}
				</form>

				{#if reveal}
					<!-- Only ever present after an explicit "Copy invite link", and never rendered as
					     text — see $lib/InvitationReveal.svelte. -->
					<InvitationReveal {reveal} who={revealWho} onclear={() => (reveal = null)} />
				{/if}
			</CardContent>
		</Card>
	</section>

	<!-- Members -->
	<section>
		<SectionHeading title="Members" count={data.members.length} />
		<Card glass>
			<CardContent>
				{#if data.members.length === 0}
					<!-- Same correction as the operations staff list: the invite form above renders for
					     everybody but its fields and its submit are `disabled={!data.canManage}`, so
					     telling a read-only operator to invite someone sends them to a form they
					     cannot fill in. -->
					<EmptyState
						compact
						icon={UsersIcon}
						title="No members yet"
						description={data.canManage
							? 'Invite someone using the form above to get started.'
							: 'This firm’s people appear here once they are invited.'}
					/>
				{:else}
					<ListingToolbar
						class="mb-4"
						bind:query
						placeholder="Search name or email"
						{dirty}
						onclear={clearFilters}
						{filterDefs}
						{filterValue}
						onfilters={onMemberFilters}
					/>
					<DataTable
						columns={memberColumns}
						rows={filteredMembers}
						bind:sort={memberSort}
						getKey={(m) => m.user_id}
						label="Members"
						empty="No members match your filters."
					>
						{#snippet cell(m, column)}
							{#if column.key === 'name'}
								<div class="flex flex-wrap items-center gap-1.5">
									<span class="font-medium">{m.name ?? m.email}</span>
									<!-- Which custodians know this person (`auth.users.provider_refs`, 0118).
									     `neutral` because it is an attribution, not a status. -->
									{#each m.providers as p (p.provider)}
										<span title="Known to {p.name} at this custodian">
											<StatusBadge tone="neutral">{p.name}</StatusBadge>
										</span>
									{/each}
									<!--
										WHAT KIND OF PERSON (`auth.users.type`, 0124) — client / staff /
										adviser, and an array because they compound: a firm's adviser is
										commonly also a client of the firm. `info` rather than `neutral` so it
										reads as a different KIND of fact from the custodian tags beside it,
										and never a good/bad tone: none of these is a status.
									-->
									{#each m.type as kind (kind)}
										<StatusBadge tone="info">{humanise(kind)}</StatusBadge>
									{/each}
									<!-- Advising is a capability WITHIN staff (0125), so it shows as its
									     own chip rather than as a third `type` member. `brand` because it
									     is the FIRM's own designation (and so re-themes with the firm),
									     visually distinct from the side-of-the-firm and custodian chips
									     beside it — and none of the three is a good/bad status. -->
									{#if m.is_adviser}
										<StatusBadge tone="brand">adviser</StatusBadge>
									{/if}
								</div>
								<div class="text-muted-foreground text-xs">{m.email}</div>
								{#if m.from_provider}
									<div class="text-muted-foreground mt-0.5 text-xs">
										Imported from {m.providers.map((p) => p.name).join(' · ') || 'a custodian'}. No
										Graphene login.
									</div>
								{/if}
								{#if accountState(m) === 'invited'}
									<StatusBadge tone="warning" class="mt-1">Invite pending</StatusBadge>
								{:else if accountState(m) === 'suspended'}
									<StatusBadge tone="danger" class="mt-1">Suspended</StatusBadge>
								{/if}
							{:else if column.key === 'client' || column.key === 'platform'}
								{@const p = column.key}
								{@const label = PORTALS.find((x) => x.key === p)?.label ?? p}
								{@const held = portalOf(m, p)}
								{@const items = roleItems(p)}
								{#if held}
									<!-- Change role within a granted portal -->
									<div class="flex flex-wrap items-center gap-2">
										<form
											method="POST"
											action="?/setRole"
											use:enhance={submitWith(`role:${m.user_id}:${p}`, 'Role updated.')}
											class="flex items-center gap-2"
										>
											<input type="hidden" name="user_id" value={m.user_id} />
											<input type="hidden" name="portal" value={p} />
											<SelectMenu
												{items}
												value={held.role_slug ?? undefined}
												name="role_slug"
												size="sm"
												class="w-auto min-w-36"
												disabled={!data.canManage}
											/>
											<Button
												type="submit"
												variant="secondary"
												size="sm"
												disabled={!data.canManage || pending[`role:${m.user_id}:${p}`]}
											>
												{pending[`role:${m.user_id}:${p}`] ? 'Setting…' : 'Set'}
											</Button>
										</form>
										<StatusBadge tone={STATUS_TONE[held.status] ?? 'danger'}>
											{STATUS_LABEL[held.status] ?? humanise(held.status)}
										</StatusBadge>
										<form
											method="POST"
											action="?/revokePortal"
											use:enhance={submitWith(`revoke:${m.user_id}:${p}`, 'Portal access revoked.')}
											bind:this={rowForms[`revoke:${m.user_id}:${p}`]}
										>
											<input type="hidden" name="user_id" value={m.user_id} />
											<input type="hidden" name="portal" value={p} />
											<Button
												type="button"
												variant="ghost"
												size="sm"
												class="text-destructive"
												disabled={!data.canManage || pending[`revoke:${m.user_id}:${p}`]}
												onclick={() => {
													revokeTarget = { member: m, portal: p, label };
													revokeOpen = true;
												}}
											>
												Revoke
											</Button>
										</form>
									</div>
								{:else}
									<!-- Grant this portal by choosing a role. A previously revoked portal
									     lands here too, so restoring one is possible without the
									     whole-person Reactivate (which restores every portal). -->
									<form
										method="POST"
										action="?/grantPortal"
										use:enhance={submitWith(`grant:${m.user_id}:${p}`, 'Portal access granted.')}
										class="flex flex-wrap items-center gap-2"
									>
										<input type="hidden" name="user_id" value={m.user_id} />
										<input type="hidden" name="portal" value={p} />
										{#if items.length === 0}
											<span class="text-muted-foreground text-sm">No {label} roles</span>
										{:else}
											{#if revokedPortal(m, p)}
												<StatusBadge tone="neutral">Revoked</StatusBadge>
											{/if}
											<SelectMenu
												{items}
												value={items[0]?.value}
												name="role_slug"
												size="sm"
												class="w-auto min-w-36"
												disabled={!data.canManage}
											/>
											<Button
												type="submit"
												variant="ghost"
												size="sm"
												disabled={!data.canManage || pending[`grant:${m.user_id}:${p}`]}
											>
												{#if pending[`grant:${m.user_id}:${p}`]}
													{revokedPortal(m, p) ? 'Restoring…' : 'Granting…'}
												{:else}
													{revokedPortal(m, p) ? 'Restore' : 'Grant'}
												{/if}
											</Button>
										{/if}
									</form>
								{/if}
							{:else if column.key === 'account'}
								<div class="flex flex-col items-end gap-2">
									{#if anyPending(m)}
										<form
											method="POST"
											action="?/resendInvite"
											use:enhance={submitWith(`resend:${m.user_id}`, (d) =>
												d?.emailDelivered
													? 'Invitation re-sent by email.'
													: 'The email was not sent. Use "Copy invite link" instead.'
											)}
										>
											<input type="hidden" name="user_id" value={m.user_id} />
											<input type="hidden" name="portal" value={pendingPortal(m)} />
											<Button
												type="submit"
												variant="ghost"
												size="sm"
												disabled={!data.canManage || pending[`resend:${m.user_id}`]}
											>
												{pending[`resend:${m.user_id}`] ? 'Resending…' : 'Resend invite'}
											</Button>
										</form>
										<form
											method="POST"
											action="?/invitationLink"
											use:enhance={submitWith(
												`link:${m.user_id}`,
												'Fresh invitation link ready. Copy it from the panel above before it expires.'
											)}
										>
											<input type="hidden" name="user_id" value={m.user_id} />
											<input type="hidden" name="portal" value={pendingPortal(m)} />
											<Button
												type="submit"
												variant="ghost"
												size="sm"
												disabled={!data.canManage || pending[`link:${m.user_id}`]}
											>
												{pending[`link:${m.user_id}`] ? 'Generating…' : 'Copy invite link'}
											</Button>
										</form>
									{/if}
									<form
										method="POST"
										action="?/resetMfa"
										use:enhance={submitWith(
											`mfa:${m.user_id}`,
											(d) =>
												`Cleared ${d?.factorsDeleted ?? 0} authenticator(s). They must set up two-factor authentication at their next sign-in.`
										)}
										bind:this={rowForms[`mfa:${m.user_id}`]}
									>
										<input type="hidden" name="user_id" value={m.user_id} />
										<Button
											type="button"
											variant="ghost"
											size="sm"
											disabled={!data.canManage || pending[`mfa:${m.user_id}`]}
											onclick={() => {
												mfaTarget = m;
												mfaOpen = true;
											}}
										>
											Reset two-factor
										</Button>
									</form>
									{#if accountState(m) !== 'suspended'}
										<form
											method="POST"
											action="?/deactivate"
											use:enhance={submitWith(`account:${m.user_id}`, 'Member deactivated.')}
											bind:this={rowForms[`deactivate:${m.user_id}`]}
										>
											<input type="hidden" name="user_id" value={m.user_id} />
											<Button
												type="button"
												variant="ghost"
												size="sm"
												class="text-destructive"
												disabled={!data.canManage || pending[`account:${m.user_id}`]}
												onclick={() => {
													deactivateTarget = m;
													deactivateOpen = true;
												}}
											>
												Deactivate
											</Button>
										</form>
									{:else}
										<form
											method="POST"
											action="?/reactivate"
											use:enhance={submitWith(`account:${m.user_id}`, reactivatedMessage)}
										>
											<input type="hidden" name="user_id" value={m.user_id} />
											<Button
												type="submit"
												variant="ghost"
												size="sm"
												disabled={!data.canManage || pending[`account:${m.user_id}`]}
											>
												{pending[`account:${m.user_id}`] ? 'Reactivating…' : 'Reactivate'}
											</Button>
										</form>
									{/if}
								</div>
							{/if}
						{/snippet}
					</DataTable>
				{/if}
			</CardContent>
		</Card>
	</section>
</div>

<ConfirmDialog
	bind:open={deactivateOpen}
	title="Deactivate this member?"
	description={deactivateTarget
		? `${deactivateTarget.name ?? deactivateTarget.email} will lose access to every portal in this firm and their active sessions will end. Reactivating restores only the access they hold now.`
		: undefined}
	confirmLabel="Deactivate"
	destructive
	onconfirm={() => {
		if (deactivateTarget) rowForms[`deactivate:${deactivateTarget.user_id}`]?.requestSubmit();
	}}
/>

<ConfirmDialog
	bind:open={mfaOpen}
	title="Reset two-factor authentication?"
	description={mfaTarget
		? `Removes every authenticator for ${mfaTarget.name ?? mfaTarget.email} and ends their sessions. Verify their identity first. They must set up two-factor authentication again at their next sign-in.`
		: undefined}
	confirmLabel="Reset two-factor"
	destructive
	onconfirm={() => {
		if (mfaTarget) rowForms[`mfa:${mfaTarget.user_id}`]?.requestSubmit();
	}}
/>

<ConfirmDialog
	bind:open={revokeOpen}
	title="Revoke portal access?"
	description={revokeTarget
		? `${revokeTarget.member.name ?? revokeTarget.member.email} will no longer be able to sign in to the ${revokeTarget.label} portal, and their live sessions end immediately. Any other portal they hold here is unaffected.`
		: undefined}
	confirmLabel="Revoke"
	destructive
	onconfirm={() => {
		if (revokeTarget)
			rowForms[`revoke:${revokeTarget.member.user_id}:${revokeTarget.portal}`]?.requestSubmit();
	}}
/>
