<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		Dialog,
		DropdownMenu,
		EmailInput,
		Input,
		Label,
		ListingSkeleton,
		MemberDirectory,
		MultiSelect,
		PageHeader,
		RecordPicker,
		Segmented,
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

	async function searchClients(query: string) {
		const url = new URL('/profiles/clients', location.origin);
		if (query) url.searchParams.set('q', query);
		const res = await fetch(url);
		if (!res.ok) throw new Error('Could not search clients.');
		return (await res.json()) as { value: string; label: string; hint?: string }[];
	}
	/** The search and the filters are the URL's, so the API answers them across every profile. */
	const navigate = (patch: Record<string, string>) =>
		goto(nextUrlFromStart(page.url, patch), {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});

	/** `nextUrlFromStart` sets one value per key — wrong for Role/Status (repeated `?role=`) and
	 *  for the date ranges (a `since`/`until` pair each), so those go through here instead. */
	const RANGE_PARAMS: Record<string, [string, string]> = {
		member_since: ['since', 'until'],
		last_active: ['active_since', 'active_until']
	};
	function navigateFilters(next: Record<string, string[]>) {
		const params = new URLSearchParams(page.url.searchParams);
		for (const [key, values] of Object.entries(next)) {
			const range = RANGE_PARAMS[key];
			if (range) {
				const [sinceKey, untilKey] = range;
				params.delete(sinceKey);
				params.delete(untilKey);
				if (values[0]) params.set(sinceKey, values[0]);
				if (values[1]) params.set(untilKey, values[1]);
			} else {
				params.delete(key);
				for (const v of values) if (v) params.append(key, v);
			}
		}
		params.delete('offset');
		const qs = params.toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
	function clearDirectory() {
		const params = new URLSearchParams(page.url.searchParams);
		for (const key of [
			'q',
			'role',
			'status',
			'since',
			'until',
			'active_since',
			'active_until',
			'offset'
		])
			params.delete(key);
		const qs = params.toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	/** Adviser and Job title are not offered here: staff-only facts a client profile never carries,
	 *  so they'd only ever filter this book to nothing. */
	const filterValue = $derived<Record<string, string[]>>({
		...(data.roleFilter.length ? { role: data.roleFilter } : {}),
		...(data.status.length ? { status: data.status } : {}),
		...(data.memberSince[0] || data.memberSince[1] ? { member_since: data.memberSince } : {}),
		...(data.lastActive[0] || data.lastActive[1] ? { last_active: data.lastActive } : {})
	});
	function profileFilterDefs(roles: OrgRole[]): FilterDef[] {
		return [
			{ key: 'role', label: 'Role', options: roleOptions(roles), allLabel: 'All roles' },
			{
				key: 'status',
				label: 'Status',
				allLabel: 'All statuses',
				options: [
					{ value: 'active', label: 'Active' },
					{ value: 'invited', label: 'Invited' },
					{ value: 'inactive', label: 'Inactive' }
				]
			},
			{ key: 'member_since', label: 'Member since', options: [], kind: 'daterange' },
			{ key: 'last_active', label: 'Last active', options: [], kind: 'daterange' }
		];
	}
	const applyFilters = (next: Record<string, string[]>) =>
		navigateFilters({
			role: next.role ?? [],
			status: next.status ?? [],
			member_since: next.member_since ?? [],
			last_active: next.last_active ?? []
		});

	const directoryRows = (profiles: Member[]): MemberDirectoryRow[] =>
		profiles.map((profile) => {
			const membership = profile.portals.find((portal) => portal.portal === 'client');
			return {
				id: profile.user_id,
				name: profile.name ?? profile.email,
				email: profile.email,
				avatar: profile.avatar,
				href: `/profiles/${profile.user_uid}`,
				createdAt: membership?.created_at ?? profile.created_at,
				lastSeen: profile.last_sign_in,
				status:
					membership?.status === 'pending' ? 'invited' : profile.enabled ? 'active' : 'inactive'
			};
		});
	let inviteRoles = $state<string[]>([]);
	let inviteClient = $state('');
	let profileSource = $state<'existing' | 'new'>('existing');
	let inviteOpen = $state(false);
	let pendingKey = $state<string | null>(null);
	const submitForm = (id: string) =>
		(document.getElementById(id) as HTMLFormElement | null)?.requestSubmit();

	function errorOf(result: ActionResult): string {
		return result.type === 'failure'
			? String(result.data?.error ?? 'Something went wrong.')
			: 'Something went wrong.';
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

	function profileInviteToast(): SubmitFunction {
		return () => {
			pendingKey = 'invite';
			return async ({ result, update }) => {
				pendingKey = null;
				if (result.type !== 'success') toast.error(errorOf(result));
				else if ((result.data as { emailDelivered?: boolean } | undefined)?.emailDelivered) {
					toast.success('Profile invitation sent.');
					inviteClient = '';
					inviteRoles = [];
					profileSource = 'existing';
					inviteOpen = false;
				} else {
					toast.error(
						'The profile was created and linked, but the invitation email could not be delivered.'
					);
					inviteOpen = false;
				}
				await update();
			};
		};
	}
</script>

<svelte:head><title>Profiles</title></svelte:head>

<PageHeader
	title="Profiles"
	description="People created for Client Portal access. Profiles can never receive staff access."
/>

{#if !data.authorised}
	<Alert variant="destructive">
		<AlertDescription>
			You do not have permission to view client profiles. Ask a firm administrator for access.
		</AlertDescription>
	</Alert>
{:else}
	<!-- Streamed: the profile window and client-portal roles. The create form's client picker
	     is typeahead when the dialog opens. -->
	<Streamed data={data.directory} onretry={() => invalidateAll()}>
		{#snippet pending()}
			<ListingSkeleton fill={false} actions pager={false} />
		{/snippet}
		{#snippet children(directory)}
			{#if directory.forbidden}
				<Alert variant="destructive">
					<AlertDescription>
						You do not have permission to view client profiles. Ask a firm administrator for access.
					</AlertDescription>
				</Alert>
			{:else}
				{@const options = roleOptions(directory.roles)}
				{@const filterDefs = profileFilterDefs(directory.roles)}
				<div class="space-y-5">
					{#if data.canCreate}
						<Dialog.Root bind:open={inviteOpen}>
							<Dialog.Content class="sm:max-w-xl">
								<Dialog.Header>
									<Dialog.Title>Create a profile</Dialog.Title>
									<Dialog.Description>
										Create a new client record or give an existing client access. The profile is
										always linked, so they never sign in to an empty portal.
									</Dialog.Description>
								</Dialog.Header>
								{#if data.canCreateExisting && data.canCreateNew}
									<Segmented
										options={[
											{ value: 'existing', label: 'Existing client' },
											{ value: 'new', label: 'New client' }
										]}
										value={profileSource}
										onValueChange={(value) =>
											(profileSource = value === 'new' ? 'new' : 'existing')}
									/>
								{/if}
								<form
									method="POST"
									action={profileSource === 'new' ? '?/addNewProfile' : '?/addProfile'}
									use:enhance={profileInviteToast()}
									class="grid gap-3 sm:grid-cols-2"
								>
									{#if profileSource === 'existing'}
										<div class="space-y-1 sm:col-span-2">
											<Label for="contact_uid">Client</Label>
											<RecordPicker
												id="contact_uid"
												name="contact_uid"
												search={searchClients}
												bind:value={inviteClient}
												placeholder="Search clients…"
												emptyText="No matching clients."
												capNote="Showing the closest matches. Type to narrow."
											/>
										</div>
									{:else}
										<div class="space-y-1">
											<Label for="first_name">First name</Label>
											<Input id="first_name" name="first_name" required />
										</div>
										<div class="space-y-1">
											<Label for="last_name">Last name</Label>
											<Input id="last_name" name="last_name" required />
										</div>
										<div class="space-y-1 sm:col-span-2">
											<Label for="email">Email</Label>
											<EmailInput id="email" name="email" required />
										</div>
									{/if}
									<div class="space-y-1 sm:col-span-2">
										<Label for="profile_role_slugs">Roles</Label>
										<MultiSelect
											id="profile_role_slugs"
											name="role_slugs"
											{options}
											bind:value={inviteRoles}
											placeholder="Choose roles…"
											searchPlaceholder="Search client roles…"
										/>
									</div>
									<Dialog.Footer class="sm:col-span-2">
										<Button type="button" variant="outline" onclick={() => (inviteOpen = false)}>
											Cancel
										</Button>
										<Button
											type="submit"
											disabled={pendingKey === 'invite' ||
												(profileSource === 'existing' && !inviteClient) ||
												inviteRoles.length === 0}
										>
											{pendingKey === 'invite' ? 'Sending…' : 'Send invitation'}
										</Button>
									</Dialog.Footer>
								</form>
							</Dialog.Content>
						</Dialog.Root>
					{/if}

					<MemberDirectory
						title="Profiles"
						description={`${directory.total} client ${
							directory.total === 1 ? 'profile' : 'profiles'
						}`}
						rows={directoryRows(directory.profiles)}
						query={data.query}
						total={directory.total}
						onsearch={(q) => navigate({ q })}
						onclear={clearDirectory}
						searchPlaceholder="Search profiles"
						emptyTitle="No profiles yet"
						emptyDescription="Client profiles appear here once they are created."
						{filterDefs}
						{filterValue}
						onfilters={applyFilters}
					>
						{#snippet listingActions()}
							{#if data.canCreate}
								<Button
									onclick={() => {
										profileSource = data.canCreateExisting ? 'existing' : 'new';
										inviteOpen = true;
									}}
								>
									<UserPlusIcon />
									Create profile
								</Button>
							{/if}
						{/snippet}
						{#snippet roles(row)}
							{@const profile = directory.profiles.find(
								(candidate) => candidate.user_id === row.id
							)!}
							{@const membership = profile.portals.find((portal) => portal.portal === 'client')}
							{#if data.canManage}
								<MemberRolesForm
									userId={profile.user_id}
									initial={membership?.role_slugs ?? []}
									{options}
									submit={withToast('Roles updated', `set:${profile.user_id}`)}
									pending={pendingKey === `set:${profile.user_id}`}
									searchPlaceholder="Edit client roles"
								/>
							{:else}
								<div class="flex flex-wrap gap-1">
									{#each membership?.role_slugs ?? [] as slug (slug)}
										<Badge variant="secondary">
											{directory.roles.find((role) => role.slug === slug)?.name ?? slug}
										</Badge>
									{/each}
								</div>
							{/if}
						{/snippet}
						{#snippet actions(row)}
							{@const profile = directory.profiles.find(
								(candidate) => candidate.user_id === row.id
							)!}
							{@const membership = profile.portals.find((portal) => portal.portal === 'client')}
							{@const suspended = isSuspendedMember(profile.portals)}
							{#if data.canManage}
								<DropdownMenu.Root>
									<DropdownMenu.Trigger
										class="text-muted-foreground hover:bg-muted hover:text-foreground inline-flex size-7 items-center justify-center rounded-md"
										aria-label={`Actions for ${profile.name ?? profile.email}`}
									>
										<MoreVerticalIcon class="size-4" />
									</DropdownMenu.Trigger>
									<DropdownMenu.Content align="end" class="w-44">
										{#if membership?.status === 'pending'}
											<DropdownMenu.Item
												onclick={() => submitForm(`profile-resend-${profile.user_id}`)}
											>
												Resend invitation
											</DropdownMenu.Item>
											{#if profile.invitation_pending}
												<DropdownMenu.Item
													variant="destructive"
													onclick={() => submitForm(`profile-revoke-${profile.user_id}`)}
												>
													Revoke invitation
												</DropdownMenu.Item>
											{/if}
										{/if}
										<DropdownMenu.Item
											variant={suspended ? undefined : 'destructive'}
											onclick={() => submitForm(`profile-state-${profile.user_id}`)}
										>
											{suspended ? 'Reactivate' : 'Deactivate'}
										</DropdownMenu.Item>
									</DropdownMenu.Content>
								</DropdownMenu.Root>
								{#if membership?.status === 'pending'}
									<form
										id={`profile-resend-${profile.user_id}`}
										method="POST"
										action="?/resendInvite"
										use:enhance={withToast('Invitation resent')}
										class="hidden"
									>
										<input type="hidden" name="user_id" value={profile.user_id} />
									</form>
									{#if profile.invitation_pending}
										<form
											id={`profile-revoke-${profile.user_id}`}
											method="POST"
											action="?/revokeInvite"
											use:enhance={withToast('Invitation revoked')}
											class="hidden"
										>
											<input type="hidden" name="user_id" value={profile.user_id} />
										</form>
									{/if}
								{/if}
								<form
									id={`profile-state-${profile.user_id}`}
									method="POST"
									action={suspended ? '?/reactivate' : '?/deactivate'}
									use:enhance={withToast(suspended ? 'Profile reactivated' : 'Profile deactivated')}
									class="hidden"
								>
									<input type="hidden" name="user_id" value={profile.user_id} />
								</form>
							{/if}
						{/snippet}
					</MemberDirectory>
				</div>
			{/if}
		{/snippet}
	</Streamed>
{/if}
