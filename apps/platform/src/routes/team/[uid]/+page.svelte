<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		AuthProfile,
		Button,
		Card,
		CardContent,
		ConfirmDialog,
		DropdownMenu,
		Input,
		Label,
		PageHeader,
		SectionHeading,
		Combobox,
		SelectMenu,
		Switch,
		isSuspendedMember,
		toast
	} from '@graphene/ui';
	import MoreVerticalIcon from '@lucide/svelte/icons/ellipsis-vertical';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import XIcon from '@lucide/svelte/icons/x';
	import { tick } from 'svelte';
	import type { ActionResult, SubmitFunction } from '@sveltejs/kit';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let confirm = $state<'passwordReset' | 'resetMfa' | 'deactivate' | 'revokeInvitation' | null>(
		null
	);
	let confirmOpen = $state(false);
	let forms: Record<string, HTMLFormElement | undefined> = {};
	let editing = $state(false);
	let editingFca = $state(false);
	let saving = $state(false);
	let savingAdviser = $state(false);
	let adviserNext = $state(false);
	let adviserPending = $state<boolean | null>(null);
	const adviserShown = $derived(adviserPending ?? Boolean(data.profile.user.is_adviser));
	let adviserForm = $state<HTMLFormElement | undefined>(undefined);
	let adviserOffOpen = $state(false);

	const dash = (value: string | null | undefined) => value?.trim() || '—';
	type OrgOption = { value: string; label: string; hint?: string };
	const orgLabel = (options: OrgOption[], value: string | null | undefined) =>
		options.find((item) => item.value === value)?.label ?? dash(value);
	const orgItems = (options: OrgOption[], current: string | null | undefined): OrgOption[] => {
		const items = [{ value: '', label: 'None' }, ...options];
		if (current && !options.some((item) => item.value === current))
			items.push({ value: current, label: current });
		return items;
	};
	const ask = (action: 'passwordReset' | 'resetMfa' | 'deactivate' | 'revokeInvitation') => {
		confirm = action;
		confirmOpen = true;
	};
	const toggleEdit = () => {
		editing = !editing;
	};

	const requestAdviser = async (next: boolean) => {
		if (!data.canManage || savingAdviser) return;
		if (next === Boolean(data.profile.user.is_adviser)) return;
		// Asks only when it changes a client: turning the flag off moves this person's `adviser`
		// rows to Support, so someone who is on teams only in other roles has nothing to confirm.
		if (!next && data.profile.staff_access.some((c) => c.role === 'adviser')) {
			adviserOffOpen = true;
			return;
		}
		adviserPending = next;
		adviserNext = next;
		await tick();
		adviserForm?.requestSubmit();
	};

	const submit =
		(action: string, success: string): SubmitFunction =>
		() => {
			return async ({ result, update }: { result: ActionResult; update: () => Promise<void> }) => {
				if (result.type === 'success') {
					const delivered = (result.data as { emailDelivered?: boolean } | undefined)
						?.emailDelivered;
					if (action === 'passwordReset' && !delivered)
						toast.error('The reset was created, but the email could not be delivered.');
					else toast.success(success);
				} else
					toast.error(
						result.type === 'failure'
							? String(result.data?.error ?? 'Something went wrong.')
							: 'Something went wrong.'
					);
				await update();
			};
		};
</script>

<PageHeader
	eyebrow="Team member"
	title={data.profile.user.name ?? data.profile.user.email}
	description={data.profile.user.email}
	back={{ href: '/team', label: 'Team' }}
>
	{#snippet actions()}
		{#if data.canManage}
			<DropdownMenu.Root>
				<DropdownMenu.Trigger
					class="border-input bg-background hover:bg-accent inline-flex h-8 items-center gap-2 rounded-lg border px-3 text-xs font-medium"
				>
					<MoreVerticalIcon class="size-4" /> Manage
				</DropdownMenu.Trigger>
				<DropdownMenu.Content align="end">
					<DropdownMenu.Item onclick={() => ask('passwordReset')}>
						Send password reset
					</DropdownMenu.Item>
					<DropdownMenu.Item onclick={() => ask('resetMfa')}>Reset two-factor</DropdownMenu.Item>
					{#if data.profile.user.invitation_pending}
						<DropdownMenu.Item variant="destructive" onclick={() => ask('revokeInvitation')}>
							Revoke invitation
						</DropdownMenu.Item>
					{/if}
					{#if isSuspendedMember(data.profile.memberships)}
						<DropdownMenu.Item onclick={() => forms.reactivate?.requestSubmit()}>
							Reactivate
						</DropdownMenu.Item>
					{:else}
						<DropdownMenu.Item variant="destructive" onclick={() => ask('deactivate')}>
							Deactivate
						</DropdownMenu.Item>
					{/if}
				</DropdownMenu.Content>
			</DropdownMenu.Root>
		{/if}
	{/snippet}
</PageHeader>

<div class="space-y-6">
	<div class="grid items-stretch gap-6 md:grid-cols-2">
		<section class="flex flex-col">
			<SectionHeading title="Staff profile">
				{#snippet actions()}
					{#if data.canManage}
						<Button
							variant="ghost"
							size="icon-sm"
							onclick={toggleEdit}
							aria-pressed={editing}
							title={editing ? 'Stop editing staff profile' : 'Edit staff profile'}
						>
							{#if editing}
								<XIcon class="size-4" />
							{:else}
								<PencilIcon class="text-brand size-4" />
							{/if}
							<span class="sr-only">
								{editing ? 'Stop editing staff profile' : 'Edit staff profile'}
							</span>
						</Button>
					{/if}
				{/snippet}
			</SectionHeading>
			<Card glass class="flex-1">
				<CardContent>
					{#if editing && data.canManage}
						<form
							method="POST"
							action="?/updateUser"
							use:enhance={() => {
								saving = true;
								return async ({ result, update }) => {
									saving = false;
									if (result.type === 'success') {
										toast.success('Profile updated.');
										editing = false;
									} else
										toast.error(
											result.type === 'failure'
												? String(result.data?.error ?? 'Something went wrong.')
												: 'Something went wrong.'
										);
									await update();
								};
							}}
							class="grid gap-3"
						>
							<div class="grid gap-3 sm:grid-cols-2">
								<div class="space-y-1">
									<Label for="edit_first_name">First name</Label>
									<Input
										id="edit_first_name"
										name="first_name"
										value={data.profile.user.first_name ?? ''}
									/>
								</div>
								<div class="space-y-1">
									<Label for="edit_last_name">Last name</Label>
									<Input
										id="edit_last_name"
										name="last_name"
										value={data.profile.user.last_name ?? ''}
									/>
								</div>
							</div>
							<div class="space-y-1">
								<Label for="edit_job_title">Job title</Label>
								<Input
									id="edit_job_title"
									name="job_title"
									value={data.profile.user.job_title ?? ''}
									placeholder="e.g. Chartered Financial Planner"
								/>
							</div>
							<div class="flex justify-end">
								<Button type="submit" disabled={saving}>
									{saving ? 'Saving…' : 'Save'}
								</Button>
							</div>
						</form>
					{:else}
						<dl class="grid gap-4 sm:grid-cols-2">
							<div>
								<dt class="text-muted-foreground text-xs">First name</dt>
								<dd class="mt-0.5 text-xs">{dash(data.profile.user.first_name)}</dd>
							</div>
							<div>
								<dt class="text-muted-foreground text-xs">Last name</dt>
								<dd class="mt-0.5 text-xs">{dash(data.profile.user.last_name)}</dd>
							</div>
							<div>
								<dt class="text-muted-foreground text-xs">Job title</dt>
								<dd class="mt-0.5 text-xs">{dash(data.profile.user.job_title)}</dd>
							</div>
						</dl>
					{/if}
				</CardContent>
			</Card>
		</section>

		<section class="flex flex-col">
			<SectionHeading title="Adviser">
				{#snippet actions()}
					{#if data.canManage}
						<Switch
							checked={adviserShown}
							disabled={savingAdviser}
							aria-label="Adviser"
							onCheckedChange={(next) => requestAdviser(Boolean(next))}
						/>
					{/if}
				{/snippet}
			</SectionHeading>
			<Card glass class="flex-1">
				<CardContent>
					{#if data.profile.user.is_adviser}
						{#if editingFca && data.canManage}
							<form
								method="POST"
								action="?/setAdviser"
								use:enhance={() => {
									savingAdviser = true;
									return async ({ result, update }) => {
										savingAdviser = false;
										if (result.type === 'success') {
											toast.success('Adviser details updated.');
											editingFca = false;
										} else
											toast.error(
												result.type === 'failure'
													? String(result.data?.error ?? 'Something went wrong.')
													: 'Something went wrong.'
											);
										await update();
									};
								}}
								class="grid gap-3"
							>
								<input type="hidden" name="is_adviser" value="on" />
								<div class="space-y-1">
									<Label for="edit_fca">FCA reference</Label>
									<Input
										id="edit_fca"
										name="fca_reference"
										value={data.profile.user.fca_reference ?? ''}
									/>
								</div>
								{#if data.providerOrgs.seccl.length}
									<div class="space-y-1">
										<Label for="seccl-node">Seccl node</Label>
										<SelectMenu
											id="seccl-node"
											name="seccl_node"
											items={orgItems(data.providerOrgs.seccl, data.profile.user.seccl_node)}
											value={data.profile.user.seccl_node ?? ''}
										/>
									</div>
								{/if}
								{#if data.providerOrgs.sei.length}
									<div class="space-y-1">
										<Label for="sei-dsr">SEI DSR</Label>
										<Combobox
											id="sei-dsr"
											name="sei_dsr"
											options={orgItems(data.providerOrgs.sei, data.profile.user.sei_dsr)}
											value={data.profile.user.sei_dsr ?? ''}
											placeholder="Search employees…"
											customNote=""
										/>
										<p class="text-muted-foreground text-xs">
											Which SEI employee this person maps to. The list is SEI's own ids and names.
										</p>
									</div>
								{/if}
								<div class="flex justify-end">
									<Button type="button" variant="ghost" onclick={() => (editingFca = false)}>
										Cancel
									</Button>
									<Button type="submit" disabled={savingAdviser}>
										{savingAdviser ? 'Saving…' : 'Save'}
									</Button>
								</div>
							</form>
						{:else}
							<div class="flex items-start justify-between gap-3">
								<div class="grid gap-3">
									<div>
										<p class="text-muted-foreground text-xs">FCA reference</p>
										<p class="mt-0.5 text-xs">{dash(data.profile.user.fca_reference)}</p>
									</div>
									{#if data.providerOrgs.seccl.length}
										<div>
											<p class="text-muted-foreground text-xs">Seccl node</p>
											<p class="mt-0.5 text-xs">
												{orgLabel(data.providerOrgs.seccl, data.profile.user.seccl_node)}
											</p>
										</div>
									{/if}
									{#if data.providerOrgs.sei.length}
										<div>
											<p class="text-muted-foreground text-xs">SEI DSR</p>
											<p class="mt-0.5 text-xs">
												{orgLabel(data.providerOrgs.sei, data.profile.user.sei_dsr)}
											</p>
										</div>
									{/if}
								</div>
								{#if data.canManage}
									<Button
										variant="ghost"
										size="icon-sm"
										onclick={() => (editingFca = true)}
										title="Edit adviser details"
									>
										<PencilIcon class="text-brand size-4" />
										<span class="sr-only">Edit adviser details</span>
									</Button>
								{/if}
							</div>
						{/if}
					{:else}
						<p class="text-muted-foreground text-sm">Off. They cannot be assigned a client book.</p>
					{/if}
				</CardContent>
			</Card>
		</section>
	</div>

	<AuthProfile profile={data.profile} canRevokeSessions={data.canManage} />
</div>

{#if data.canManage}
	<form
		method="POST"
		action="?/passwordReset"
		use:enhance={submit('passwordReset', 'Password reset sent.')}
		bind:this={forms.passwordReset}
		class="hidden"
	></form>
	<form
		method="POST"
		action="?/revokeInvitation"
		use:enhance={submit('revokeInvitation', 'Invitation revoked.')}
		bind:this={forms.revokeInvitation}
		class="hidden"
	></form>
	<form
		method="POST"
		action="?/resetMfa"
		use:enhance={submit('resetMfa', 'Two-factor reset.')}
		bind:this={forms.resetMfa}
		class="hidden"
	></form>
	<form
		method="POST"
		action="?/deactivate"
		use:enhance={submit('deactivate', 'Team member deactivated.')}
		bind:this={forms.deactivate}
		class="hidden"
	></form>
	<form
		method="POST"
		action="?/reactivate"
		use:enhance={submit('reactivate', 'Team member reactivated.')}
		bind:this={forms.reactivate}
		class="hidden"
	></form>
	<form
		method="POST"
		action="?/setAdviser"
		bind:this={adviserForm}
		class="hidden"
		use:enhance={() => {
			savingAdviser = true;
			return async ({ result, update }) => {
				savingAdviser = false;
				if (result.type === 'success')
					toast.success(adviserNext ? 'Adviser enabled.' : 'Adviser turned off.');
				else {
					adviserPending = null;
					toast.error(
						result.type === 'failure'
							? String(result.data?.error ?? 'Something went wrong.')
							: 'Something went wrong.'
					);
				}
				await update();
				adviserPending = null;
			};
		}}
	>
		<input type="hidden" name="is_adviser" value={adviserNext ? 'on' : ''} />
	</form>
{/if}

<ConfirmDialog
	bind:open={confirmOpen}
	title={confirm === 'passwordReset'
		? 'Send a password reset?'
		: confirm === 'resetMfa'
			? 'Reset two-factor authentication?'
			: confirm === 'revokeInvitation'
				? 'Revoke this invitation?'
				: 'Deactivate this team member?'}
	description={confirm === 'passwordReset'
		? 'A fresh reset link will be emailed and every live session will end.'
		: confirm === 'resetMfa'
			? 'Verify their identity first. Every authenticator and live session will be removed.'
			: confirm === 'revokeInvitation'
				? 'The current invitation link will stop working. You can send a fresh invitation later.'
				: 'They will lose all portal access for this firm and their live sessions will end.'}
	confirmLabel={confirm === 'passwordReset'
		? 'Send reset'
		: confirm === 'resetMfa'
			? 'Reset two-factor'
			: confirm === 'revokeInvitation'
				? 'Revoke invitation'
				: 'Deactivate'}
	destructive
	onconfirm={() => {
		if (confirm) forms[confirm]?.requestSubmit();
		confirm = null;
	}}
/>

<ConfirmDialog
	bind:open={adviserOffOpen}
	title="Turn adviser off?"
	description="They will stop being the adviser on their clients and stay on each client's team as Support."
	confirmLabel="Turn off"
	destructive
	onconfirm={async () => {
		adviserPending = false;
		adviserNext = false;
		await tick();
		adviserForm?.requestSubmit();
	}}
/>
