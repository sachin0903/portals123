<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import {
		Badge,
		Button,
		Card,
		CardContent,
		ConfirmDialog,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		EmptyState,
		Input,
		EmailInput,
		Label,
		PageHeader,
		SectionHeading,
		SelectMenu,
		StatusBadge,
		toast
	} from '@graphene/ui';
	import UsersIcon from '@lucide/svelte/icons/users';
	import type { SubmitFunction } from '@sveltejs/kit';
	import type { PageData, ActionData } from './$types';
	import InvitationReveal from '$lib/InvitationReveal.svelte';
	import { startReveal, type Reveal } from '$lib/invitation';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	// Only ever populated by the explicit "Copy invite link" action — the set-password URL sets
	// somebody's password, so it must not ride along on the invite/resend responses. Held in local
	// state rather than read from `form`, so it can be dropped once copied or once its TTL lapses
	// (an ops portal is exactly where a live credential must not sit on screen).
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

	const roleItems = $derived(data.roleOptions.map((r) => ({ value: r.slug, label: r.name })));
	// Native selects defaulted to their first option; keep that behaviour.
	let inviteRole = $state(untrack(() => data.roleOptions[0]?.slug ?? ''));

	type StaffMember = (typeof data.members)[number];
	// Role is edited inline (a dropdown when the reader can manage, otherwise plain text) but
	// still names a real slug to order by; a pending invite carries no slug yet, and blanks sort
	// last regardless of direction. Actions stays unsorted like every actions column.
	const staffColumns: DataTableColumn<StaffMember>[] = [
		{
			key: 'name',
			header: 'Person',
			sortable: true,
			sortDefault: true,
			sortValue: (m) => m.name ?? m.email
		},
		{ key: 'role_slug', header: 'Role', sortable: true, sortValue: (m) => m.role_slug ?? '' },
		{ key: 'actions', header: 'Actions', align: 'right' }
	];
	let staffSort = $state<DataTableSort | null>(null);

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

	// Deactivation confirms first; requestSubmit keeps the form-action wiring.
	let rowForms: Record<string, HTMLFormElement | undefined> = {};
	let deactivateOpen = $state(false);
	let deactivateTarget = $state<(typeof data.members)[number] | null>(null);
	let mfaOpen = $state(false);
	let mfaTarget = $state<(typeof data.members)[number] | null>(null);
</script>

<PageHeader
	eyebrow="Internal operations"
	title="Operations staff"
	description="Graphene internal-operations team."
>
	{#snippet actions()}
		{#if data.canManage}
			<form
				method="POST"
				action="?/sync"
				use:enhance={submitWith('sync', (d) => `Synced ${d?.members ?? 0} member(s) from WorkOS.`)}
			>
				<Button type="submit" variant="secondary" size="sm" disabled={pending.sync}>
					{pending.sync ? 'Syncing…' : 'Sync from WorkOS'}
				</Button>
			</form>
		{/if}
	{/snippet}
</PageHeader>

<div class="space-y-5">
	<!-- A revealed invitation link. Its own top-level block, not nested in the invite card: the
	     button that asks for it is a row action, and the invite card hides itself when the ops
	     tenant has no roles yet — which would have made the panel unreachable. -->
	{#if reveal}
		<!-- Never rendered as text — see $lib/InvitationReveal.svelte. -->
		<InvitationReveal {reveal} who={revealWho} onclear={() => (reveal = null)} />
	{/if}

	<!-- Invite a staff member -->
	{#if data.canManage}
		<section>
			<SectionHeading title="Invite a staff member" />
			<Card glass>
				<CardContent class="space-y-3">
					<p class="text-muted-foreground text-sm">
						Adds them to the Internal Operations org and emails a link to set a password. If the
						email doesn't arrive, use "Copy invite link" on their row.
					</p>
					{#if data.roleOptions.length === 0}
						<p class="text-muted-foreground text-sm">
							Create an operations role first (Roles page, sidebar) before inviting staff.
						</p>
					{:else}
						<form
							method="POST"
							action="?/addMember"
							use:enhance={submitWith(
								'invite',
								(d) =>
									d?.emailDelivered
										? `Invitation emailed to ${d?.email}.`
										: 'Staff member created, but the invitation email was not sent. Use "Copy invite link" on their row.',
								{ reset: true }
							)}
							class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
						>
							<div class="space-y-1">
								<Label for="first_name">First name</Label>
								<Input id="first_name" name="first_name" placeholder="Ada" />
							</div>
							<div class="space-y-1">
								<Label for="last_name">Last name</Label>
								<Input id="last_name" name="last_name" placeholder="Lovelace" />
							</div>
							<div class="space-y-1">
								<Label for="email">Email</Label>
								<EmailInput id="email" name="email" placeholder="ada@graphene…" required />
							</div>
							<div class="space-y-1">
								<Label for="role_slug">Role</Label>
								<SelectMenu
									id="role_slug"
									name="role_slug"
									items={roleItems}
									bind:value={inviteRole}
									placeholder="Choose a role…"
								/>
							</div>
							<div class="sm:col-span-2 lg:col-span-4">
								<Button type="submit" disabled={pending.invite}>
									{pending.invite ? 'Sending…' : 'Send invitation'}
								</Button>
							</div>
						</form>
					{/if}
				</CardContent>
			</Card>
		</section>
	{/if}

	<!-- Staff -->
	<section>
		<SectionHeading title="Staff" count={data.members.length} />
		<Card glass>
			<CardContent>
				{#if data.members.length === 0}
					<!-- "Invite someone above" was unconditional while the invite section it points at is
					     `{#if data.canManage}` — so a reader without operations:manage was told to use
					     a form that is not on their page. My own sweep waved this through as "points
					     at a form on the same screen"; it does not, for the reader who most needs to
					     be told the truth. -->
					<EmptyState
						compact
						icon={UsersIcon}
						title="No ops staff yet"
						description={data.canManage
							? 'Invite someone using the form above to get started.'
							: 'Graphene operations staff appear here once they are invited.'}
					/>
				{:else}
					<DataTable
						columns={staffColumns}
						rows={data.members}
						bind:sort={staffSort}
						getKey={(m) => m.user_id}
						label="Staff"
					>
						{#snippet cell(m, column)}
							{#if column.key === 'name'}
								<a class="font-medium hover:underline" href="/operations/users/{m.user_uid}">
									{m.name ?? '—'}
								</a>
								<div class="text-muted-foreground text-xs">{m.email}</div>
							{:else if column.key === 'role_slug'}
								{#if m.status === 'pending'}
									<div class="flex items-center gap-2">
										<Badge variant="outline">{m.role_slug ?? '—'}</Badge>
										<StatusBadge tone="warning">Invited</StatusBadge>
									</div>
								{:else if data.canManage}
									<form
										method="POST"
										action="?/changeRole"
										use:enhance={submitWith(`role:${m.user_id}`, 'Role updated.')}
										class="flex items-center gap-2"
									>
										<input type="hidden" name="user_id" value={m.user_id} />
										<SelectMenu
											items={roleItems}
											value={m.role_slug ?? undefined}
											name="role_slug"
											size="sm"
											class="w-auto min-w-36"
										/>
										<Button
											type="submit"
											variant="secondary"
											size="sm"
											disabled={pending[`role:${m.user_id}`]}
										>
											{pending[`role:${m.user_id}`] ? 'Setting…' : 'Set'}
										</Button>
									</form>
								{:else}
									<span class="text-sm">{m.role_slug ?? '—'}</span>
								{/if}
							{:else if column.key === 'actions'}
								{#if !data.canManage}
									<span class="text-muted-foreground text-sm">—</span>
								{:else}
									<div class="flex flex-col items-end gap-2">
										{#if m.status === 'pending'}
											<!-- An undelivered ops invite had no recovery path at all. -->
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
												<Button
													type="submit"
													variant="ghost"
													size="sm"
													disabled={pending[`resend:${m.user_id}`]}
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
												<Button
													type="submit"
													variant="ghost"
													size="sm"
													disabled={pending[`link:${m.user_id}`]}
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
												disabled={pending[`mfa:${m.user_id}`]}
												onclick={() => {
													mfaTarget = m;
													mfaOpen = true;
												}}
											>
												Reset two-factor
											</Button>
										</form>
										{#if m.status === 'inactive'}
											<form
												method="POST"
												action="?/reactivate"
												use:enhance={submitWith(
													`account:${m.user_id}`,
													'Staff member reactivated.'
												)}
											>
												<input type="hidden" name="user_id" value={m.user_id} />
												<Button
													type="submit"
													variant="ghost"
													size="sm"
													disabled={pending[`account:${m.user_id}`]}
												>
													{pending[`account:${m.user_id}`] ? 'Reactivating…' : 'Reactivate'}
												</Button>
											</form>
										{:else}
											<form
												method="POST"
												action="?/deactivate"
												use:enhance={submitWith(
													`account:${m.user_id}`,
													'Staff member deactivated.'
												)}
												bind:this={rowForms[`deactivate:${m.user_id}`]}
											>
												<input type="hidden" name="user_id" value={m.user_id} />
												<Button
													type="button"
													variant="ghost"
													size="sm"
													class="text-destructive"
													disabled={pending[`account:${m.user_id}`]}
													onclick={() => {
														deactivateTarget = m;
														deactivateOpen = true;
													}}
												>
													Deactivate
												</Button>
											</form>
										{/if}
									</div>
								{/if}
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
	title="Deactivate this staff member?"
	description={deactivateTarget
		? `${deactivateTarget.name ?? deactivateTarget.email} loses access to Internal Operations.`
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
