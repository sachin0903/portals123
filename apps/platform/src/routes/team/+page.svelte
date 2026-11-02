<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		ConfirmDialog,
		Dialog,
		DropdownMenu,
		EmailInput,
		Input,
		Label,
		ListingSkeleton,
		MemberDirectory,
		MultiSelect,
		PageHeader,
		Streamed,
		isSuspendedMember,
		type MemberDirectoryRow,
		toast
	} from '@graphene/ui';
	import MoreVerticalIcon from '@lucide/svelte/icons/ellipsis-vertical';
	import UserPlusIcon from '@lucide/svelte/icons/user-plus';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { nextUrlFromStart, type FilterDef } from '@graphene/ui/book/listing';
	import MemberRolesForm from '$lib/components/members/member-roles-form.svelte';
	import type { Member, OrgRole } from '$lib/api';
	import { roleOptions } from '$lib/roles';
	import type { ActionResult, SubmitFunction } from '@sveltejs/kit';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	/** The roles a staff membership may hold. A role attached from the other staff portal (the
	 *  drift 0120 warns about) is not offered, but MultiSelect renders its slug as a chip, so the
	 *  row shows what is set and saving here replaces it with a Platform role. */
	const staffRoles = (roles: OrgRole[]) => roles.filter((role) => role.portal === 'platform');
	const directoryRows = (members: Member[]): MemberDirectoryRow[] =>
		members.map((member) => {
			const membership = member.portals.find((portal) => portal.portal === 'platform');
			return {
				id: member.user_id,
				name: member.name ?? member.email,
				email: member.email,
				avatar: member.avatar,
				href: `/team/${member.user_uid}`,
				createdAt: membership?.created_at ?? member.created_at,
				lastSeen: member.last_sign_in,
				status:
					membership?.status === 'pending' ? 'invited' : member.enabled ? 'active' : 'inactive',
				isAdviser: member.is_adviser,
				jobTitle: member.job_title
			};
		});
	let inviteRoles = $state<string[]>([]);

	/** The search is the URL's, so the API answers it across every staff member. */
	const navigate = (patch: Record<string, string>) =>
		goto(nextUrlFromStart(page.url, patch), {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});

	/** The role filter's own navigation: `nextUrlFromStart` sets one value per key, and a
	 *  multi-select filter is several — repeated params, appended rather than set. */
	function navigateRole(roles: string[]) {
		const params = new URLSearchParams(page.url.searchParams);
		params.delete('role');
		params.delete('offset');
		for (const r of roles) if (r) params.append('role', r);
		const qs = params.toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
	function clearDirectory() {
		const params = new URLSearchParams(page.url.searchParams);
		params.delete('q');
		params.delete('role');
		params.delete('offset');
		const qs = params.toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	const roleFilterValue = $derived<Record<string, string[]>>(
		data.roleFilter.length ? { role: data.roleFilter } : {}
	);
	function roleFilterDefs(roles: OrgRole[]): FilterDef[] {
		return [
			{
				key: 'role',
				label: 'Role',
				options: roleOptions(staffRoles(roles)),
				allLabel: 'All roles'
			}
		];
	}
	const applyRoleFilter = (next: Record<string, string[]>) => navigateRole(next.role ?? []);

	// Toast-driven feedback for the row mutations; pending keys disable the busy control.
	function errorOf(result: ActionResult): string {
		return result.type === 'failure'
			? String(result.data?.error ?? 'Something went wrong.')
			: 'Something went wrong.';
	}
	let pendingKey = $state<string | null>(null);

	// Resending an invite is only a success if the email actually left: the API reports
	// `email_delivered`, and a toast that says "Invitation resent" when Resend refused it sends the
	// adviser away believing the person can now get in.
	function resendToast(userId: string): SubmitFunction {
		return () => {
			pendingKey = `resend:${userId}`;
			return async ({ result, update }) => {
				pendingKey = null;
				if (result.type === 'success') {
					if ((result.data as { emailDelivered?: boolean } | undefined)?.emailDelivered)
						toast.success('Invitation resent.');
					else
						toast.error(
							'A fresh link was created but the invitation email could not be delivered.'
						);
				} else toast.error(errorOf(result));
				await update();
			};
		};
	}

	function withToast(success: string, key?: string): SubmitFunction {
		return () => {
			pendingKey = key ?? null;
			return async ({ result, update }) => {
				pendingKey = null;
				if (result.type === 'success') toast.success(success);
				else toast.error(errorOf(result));
				await update();
			};
		};
	}

	// Invite dialog pending state.
	let inviting = $state(false);
	let inviteOpen = $state(false);

	// Deactivation is destructive: the row button arms the member, one hidden form submits.
	let deactivateOpen = $state(false);
	let deactivateTarget = $state<Member | null>(null);
	let deactivateFormEl = $state<HTMLFormElement | null>(null);
	const submitForm = (id: string) =>
		(document.getElementById(id) as HTMLFormElement | null)?.requestSubmit();
</script>

<PageHeader
	title="Team"
	description="Firm staff who work in the platform. Client access is managed separately under Profiles."
/>

{#if !data.authorised}
	<Alert variant="destructive">
		<AlertDescription>
			You do not have permission to view your firm's team. Ask a firm administrator for access.
		</AlertDescription>
	</Alert>
{:else}
	<!-- Streamed: the directory is two API reads (the staff and the firm's roles), so the page's shape
	     lands on the click and the rows follow. -->
	<Streamed data={data.team} onretry={() => invalidateAll()}>
		{#snippet pending()}
			<ListingSkeleton fill={false} actions pager={false} />
		{/snippet}
		{#snippet children(team)}
			{#if team.forbidden}
				<Alert variant="destructive">
					<AlertDescription>
						You do not have permission to view your firm's team. Ask a firm administrator for
						access.
					</AlertDescription>
				</Alert>
			{:else}
				{@const options = roleOptions(staffRoles(team.roles))}
				{@const filterDefs = roleFilterDefs(team.roles)}
				<div class="space-y-5">
					{#if data.canManage}
						<!-- Invite a person -->
						<Dialog.Root bind:open={inviteOpen}>
							<Dialog.Content class="sm:max-w-xl">
								<Dialog.Header>
									<Dialog.Title>Invite a member</Dialog.Title>
									<Dialog.Description>
										Adds a staff member with access to this workspace. Choose the roles they need.
									</Dialog.Description>
								</Dialog.Header>
								<form
									method="POST"
									action="?/addMember"
									use:enhance={() => {
										inviting = true;
										return async ({ result, update }) => {
											inviting = false;
											if (result.type !== 'success') toast.error(errorOf(result));
											else {
												const delivered = (result.data as { emailDelivered?: boolean } | undefined)
													?.emailDelivered;
												if (delivered) toast.success('Invitation sent.');
												else
													toast.error(
														'The member was created, but the invitation email could not be delivered.'
													);
												inviteOpen = false;
											}
											await update();
										};
									}}
									class="grid gap-3 sm:grid-cols-2"
								>
									<div class="space-y-1">
										<Label for="first_name">First name</Label>
										<Input id="first_name" name="first_name" />
									</div>
									<div class="space-y-1">
										<Label for="last_name">Last name</Label>
										<Input id="last_name" name="last_name" />
									</div>
									<div class="space-y-1">
										<Label for="email">Email</Label>
										<EmailInput id="email" name="email" placeholder="person@firm.com" />
									</div>
									<div class="space-y-1">
										<Label for="role_slugs">Roles</Label>
										<MultiSelect
											id="role_slugs"
											name="role_slugs"
											{options}
											bind:value={inviteRoles}
											placeholder="Choose roles…"
											searchPlaceholder="Search staff roles…"
										/>
									</div>
									<Dialog.Footer class="sm:col-span-2">
										<Button type="button" variant="outline" onclick={() => (inviteOpen = false)}>
											Cancel
										</Button>
										<Button type="submit" disabled={inviting || inviteRoles.length === 0}>
											{inviting ? 'Sending…' : 'Send invitation'}
										</Button>
									</Dialog.Footer>
								</form>
							</Dialog.Content>
						</Dialog.Root>
					{/if}

					<!-- People -->
					<MemberDirectory
						title="Team members"
						description={`${team.total} staff ${team.total === 1 ? 'member' : 'members'}`}
						rows={directoryRows(team.members)}
						query={data.query}
						total={team.total}
						onsearch={(q) => navigate({ q })}
						onclear={clearDirectory}
						searchPlaceholder="Search team members"
						emptyTitle="No team members yet"
						emptyDescription={data.canManage
							? 'Invite someone above to get started.'
							: 'People appear here once they’re invited.'}
						{filterDefs}
						filterValue={roleFilterValue}
						onfilters={applyRoleFilter}
					>
						{#snippet listingActions()}
							{#if data.canManage}
								<Button onclick={() => (inviteOpen = true)}>
									<UserPlusIcon />
									Invite member
								</Button>
							{/if}
						{/snippet}
						{#snippet roles(row)}
							{@const member = team.members.find((candidate) => candidate.user_id === row.id)!}
							{@const membership = member.portals.find((portal) => portal.portal === 'platform')}
							{#if data.canManage}
								<MemberRolesForm
									userId={member.user_id}
									initial={membership?.role_slugs ?? []}
									{options}
									submit={withToast('Roles updated', `set:${member.user_id}`)}
									pending={pendingKey === `set:${member.user_id}`}
									searchPlaceholder="Edit staff roles"
								/>
							{:else}
								<div class="flex flex-wrap gap-1">
									{#each membership?.role_slugs ?? [] as slug (slug)}
										<Badge variant="secondary">
											{staffRoles(team.roles).find((role) => role.slug === slug)?.name ?? slug}
										</Badge>
									{/each}
								</div>
							{/if}
						{/snippet}
						{#snippet actions(row)}
							{@const member = team.members.find((candidate) => candidate.user_id === row.id)!}
							{#if data.canManage}
								<DropdownMenu.Root>
									<DropdownMenu.Trigger
										class="text-muted-foreground hover:bg-muted hover:text-foreground inline-flex size-7 items-center justify-center rounded-md"
										aria-label={`Actions for ${member.name ?? member.email}`}
									>
										<MoreVerticalIcon class="size-4" />
									</DropdownMenu.Trigger>
									<DropdownMenu.Content align="end" class="w-44">
										{#if member.portals.some((portal) => portal.status === 'pending')}
											<DropdownMenu.Item onclick={() => submitForm(`resend-${member.user_id}`)}>
												Resend invitation
											</DropdownMenu.Item>
											{#if member.invitation_pending}
												<DropdownMenu.Item
													variant="destructive"
													onclick={() => submitForm(`revoke-invite-${member.user_id}`)}
												>
													Revoke invitation
												</DropdownMenu.Item>
											{/if}
										{/if}
										{#if isSuspendedMember(member.portals)}
											<DropdownMenu.Item onclick={() => submitForm(`reactivate-${member.user_id}`)}>
												Reactivate
											</DropdownMenu.Item>
										{:else}
											<DropdownMenu.Item
												variant="destructive"
												onclick={() => {
													deactivateTarget = member;
													deactivateOpen = true;
												}}
											>
												Deactivate
											</DropdownMenu.Item>
										{/if}
									</DropdownMenu.Content>
								</DropdownMenu.Root>
								{#if member.portals.some((portal) => portal.status === 'pending')}
									<form
										id={`resend-${member.user_id}`}
										method="POST"
										action="?/resendInvite"
										use:enhance={resendToast(member.user_id)}
										class="hidden"
									>
										<input type="hidden" name="user_id" value={member.user_id} />
									</form>
									{#if member.invitation_pending}
										<form
											id={`revoke-invite-${member.user_id}`}
											method="POST"
											action="?/revokeInvite"
											use:enhance={withToast('Invitation revoked')}
											class="hidden"
										>
											<input type="hidden" name="user_id" value={member.user_id} />
										</form>
									{/if}
								{/if}
								{#if isSuspendedMember(member.portals)}
									<form
										id={`reactivate-${member.user_id}`}
										method="POST"
										action="?/reactivate"
										use:enhance={withToast('Member reactivated', `react:${member.user_id}`)}
										class="hidden"
									>
										<input type="hidden" name="user_id" value={member.user_id} />
									</form>
								{/if}
							{/if}
						{/snippet}
					</MemberDirectory>
				</div>
			{/if}
		{/snippet}
	</Streamed>

	<!-- Deactivation: one hidden form serves every row; the dialog submits it. -->
	<form
		method="POST"
		action="?/deactivate"
		bind:this={deactivateFormEl}
		use:enhance={withToast('Member deactivated')}
		class="hidden"
	>
		<input type="hidden" name="user_id" value={deactivateTarget?.user_id ?? ''} />
	</form>
	<ConfirmDialog
		bind:open={deactivateOpen}
		title="Deactivate {deactivateTarget?.name ?? deactivateTarget?.email ?? 'this member'}?"
		description="They lose Platform access. You can reactivate them later."
		confirmLabel="Deactivate"
		destructive
		onconfirm={() => deactivateFormEl?.requestSubmit()}
	/>
{/if}
