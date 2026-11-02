<script lang="ts">
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
		PageHeader,
		RecordId,
		SectionHeading,
		SelectMenu,
		StatusBadge,
		toast,
		type StatusTone
	} from '@graphene/ui';
	import BuildingIcon from '@lucide/svelte/icons/building-2';
	import type { SubmitFunction } from '@sveltejs/kit';
	import type { PageData } from './$types';
	import type { Membership } from './+page.server';

	let { data }: { data: PageData } = $props();

	const PORTAL_LABELS: Record<string, string> = {
		client: 'Client',
		platform: 'Platform',
		operations: 'Operations'
	};

	// A person can hold Client and/or Platform in the same firm, so a row is keyed by BOTH — the
	// API returns one row per (firm, portal).
	const rowKey = (m: Membership) => `${m.tenant_id}:${m.portal}`;
	// Only the roles that firm defines for that portal are assignable.
	const roleItems = (m: Membership) =>
		(data.roles[m.tenant_id] ?? [])
			.filter((r) => r.portal === m.portal)
			.map((r) => ({ value: r.slug, label: r.name }));

	const STATUS_TONE: Record<string, StatusTone> = { active: 'success', pending: 'warning' };
	const STATUS_LABEL: Record<string, string> = {
		active: 'Active',
		pending: 'Invited',
		inactive: 'Revoked'
	};

	// Firm and Portal are plain fields. Role is edited via an inline dropdown but still names a
	// real slug to order by (a firm with no roles configured for that portal reads as blank, and
	// blanks sort last regardless of direction). Actions carries the status badge alongside the
	// revoke control, and stays unsorted like every actions column.
	const membershipColumns: DataTableColumn<Membership>[] = [
		{ key: 'tenant_name', header: 'Firm', sortable: true, sortDefault: true },
		{
			key: 'portal',
			header: 'Portal',
			sortable: true,
			sortValue: (m) => PORTAL_LABELS[m.portal] ?? m.portal
		},
		{ key: 'role_slug', header: 'Role', sortable: true, sortValue: (m) => m.role_slug ?? '' },
		{ key: 'actions', header: 'Actions', align: 'right' }
	];
	let membershipSort = $state<DataTableSort | null>(null);

	// The firms this person is suspended in, so the row offers Restore rather than Suspend.
	// Grouped by the firm's UID — what the roles map and the actions are keyed by, and what the
	// admin tenant routes take. The serial `tenant_id` stays on the row for display joins.
	const firms = $derived([...new Set(data.memberships.map((m) => m.tenant_uid))]);

	// Per-form pending flags + a shared enhance callback that reports outcomes via toast.
	let pending = $state<Record<string, boolean>>({});
	function submitWith(
		key: string,
		success: string | ((data: Record<string, unknown> | undefined) => string)
	): SubmitFunction {
		return () => {
			pending[key] = true;
			return async ({ result, update }) => {
				pending[key] = false;
				if (result.type === 'success') {
					toast.success(typeof success === 'function' ? success(result.data) : success);
					await update({ reset: false });
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

	// Destructive actions confirm first; requestSubmit keeps the form-action wiring.
	let rowForms: Record<string, HTMLFormElement | undefined> = {};
	let revokeOpen = $state(false);
	let revokeTarget = $state<Membership | null>(null);
	let suspendOpen = $state(false);
	let suspendTarget = $state<{ tenantUid: string; tenantName: string } | null>(null);
	let mfaOpen = $state(false);
	let mfaTarget = $state<{ tenantUid: string; tenantName: string } | null>(null);
</script>

<PageHeader
	eyebrow="Person"
	title={data.user.name ?? data.user.email}
	description={data.user.enabled ? data.user.email : `${data.user.email} · account disabled`}
	back={{ href: '/users', label: 'Users' }}
/>

<section>
	<SectionHeading title="Firm access" count={data.memberships.length} />
	<Card glass>
		<CardContent>
			{#if data.memberships.length === 0}
				<EmptyState compact icon={BuildingIcon} title="Not a member of any firm" />
			{:else}
				<DataTable
					columns={membershipColumns}
					rows={data.memberships}
					bind:sort={membershipSort}
					getKey={rowKey}
					label="Firm access"
				>
					{#snippet cell(m, column)}
						{#if column.key === 'tenant_name'}
							<a href="/tenants/{m.tenant_uid}" class="font-medium hover:underline"
								>{m.tenant_name}</a
							>
							<RecordId value={m.tenant_alias} />
						{:else if column.key === 'portal'}
							{PORTAL_LABELS[m.portal] ?? m.portal}
						{:else if column.key === 'role_slug'}
							{@const items = roleItems(m)}
							{#if data.roleErrors.includes(m.tenant_uid)}
								<span class="text-muted-foreground text-sm">
									Roles unavailable. Your account cannot read this firm's roles.
								</span>
							{:else if items.length === 0}
								<span class="text-muted-foreground text-sm">
									This firm has no {PORTAL_LABELS[m.portal] ?? m.portal} roles
								</span>
							{:else}
								<form
									method="POST"
									action="?/changeRole"
									use:enhance={submitWith(`role:${rowKey(m)}`, 'Role updated.')}
									class="flex items-center gap-2"
								>
									<input type="hidden" name="tenant_uid" value={m.tenant_uid} />
									<input type="hidden" name="portal" value={m.portal} />
									<SelectMenu
										{items}
										value={m.role_slug ?? undefined}
										name="role_slug"
										size="sm"
										class="w-auto min-w-36"
										disabled={!data.canManage}
									/>
									<Button
										type="submit"
										variant="secondary"
										size="sm"
										disabled={!data.canManage || pending[`role:${rowKey(m)}`]}
									>
										{pending[`role:${rowKey(m)}`] ? 'Setting…' : 'Set'}
									</Button>
								</form>
							{/if}
						{:else if column.key === 'actions'}
							<div class="flex items-center justify-end gap-2">
								<StatusBadge tone={STATUS_TONE[m.status] ?? 'danger'}>
									{STATUS_LABEL[m.status] ?? m.status}
								</StatusBadge>
								{#if m.status !== 'inactive'}
									<form
										method="POST"
										action="?/revokePortal"
										use:enhance={submitWith(`revoke:${rowKey(m)}`, 'Portal access revoked.')}
										bind:this={rowForms[`revoke:${rowKey(m)}`]}
									>
										<input type="hidden" name="tenant_uid" value={m.tenant_uid} />
										<input type="hidden" name="portal" value={m.portal} />
										<Button
											type="button"
											variant="ghost"
											size="sm"
											class="text-destructive"
											disabled={!data.canManage || pending[`revoke:${rowKey(m)}`]}
											onclick={() => {
												revokeTarget = m;
												revokeOpen = true;
											}}
										>
											Revoke
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

{#if firms.length}
	<section class="mt-6">
		<SectionHeading title="Account actions" />
		<Card glass>
			<CardContent class="space-y-3">
				<p class="text-muted-foreground text-sm">
					These apply to the whole person within a firm, not to a single portal.
				</p>
				{#each firms as tenantUid (tenantUid)}
					{@const rows = data.memberships.filter((m) => m.tenant_uid === tenantUid)}
					{@const firmName = rows[0].tenant_name}
					<div
						class="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2"
					>
						<div class="min-w-0">
							<div class="truncate font-medium">{firmName}</div>
							<div class="text-muted-foreground text-xs">
								{rows.map((r) => PORTAL_LABELS[r.portal] ?? r.portal).join(' · ')}
							</div>
						</div>
						<div class="flex items-center gap-2">
							<form
								method="POST"
								action="?/resetMfa"
								use:enhance={submitWith(
									`mfa:${tenantUid}`,
									(d) =>
										`Cleared ${d?.factorsDeleted ?? 0} authenticator(s). They must set up two-factor authentication at their next sign-in.`
								)}
								bind:this={rowForms[`mfa:${tenantUid}`]}
							>
								<input type="hidden" name="tenant_uid" value={tenantUid} />
								<Button
									type="button"
									variant="secondary"
									size="sm"
									disabled={!data.canManage || pending[`mfa:${tenantUid}`]}
									onclick={() => {
										mfaTarget = { tenantUid, tenantName: firmName };
										mfaOpen = true;
									}}
								>
									Reset two-factor
								</Button>
							</form>
							{#if data.user.enabled}
								<form
									method="POST"
									action="?/suspendInFirm"
									use:enhance={submitWith(`suspend:${tenantUid}`, 'Suspended in this firm.')}
									bind:this={rowForms[`suspend:${tenantUid}`]}
								>
									<input type="hidden" name="tenant_uid" value={tenantUid} />
									<Button
										type="button"
										variant="ghost"
										size="sm"
										class="text-destructive"
										disabled={!data.canManage || pending[`suspend:${tenantUid}`]}
										onclick={() => {
											suspendTarget = { tenantUid, tenantName: firmName };
											suspendOpen = true;
										}}
									>
										Suspend in firm
									</Button>
								</form>
							{:else}
								<form
									method="POST"
									action="?/restoreInFirm"
									use:enhance={submitWith(`restore:${tenantUid}`, 'Access restored.')}
								>
									<input type="hidden" name="tenant_uid" value={tenantUid} />
									<Button
										type="submit"
										variant="ghost"
										size="sm"
										disabled={!data.canManage || pending[`restore:${tenantUid}`]}
									>
										{pending[`restore:${tenantUid}`] ? 'Restoring…' : 'Restore access'}
									</Button>
								</form>
							{/if}
						</div>
					</div>
				{/each}
			</CardContent>
		</Card>
	</section>
{/if}

<ConfirmDialog
	bind:open={revokeOpen}
	title="Revoke this portal?"
	description={revokeTarget
		? `${data.user.name ?? data.user.email} will lose ${PORTAL_LABELS[revokeTarget.portal] ?? revokeTarget.portal} access to ${revokeTarget.tenant_name}. Any other portal they hold there is unaffected, and their live sessions end immediately.`
		: undefined}
	confirmLabel="Revoke"
	destructive
	onconfirm={() => {
		if (revokeTarget) rowForms[`revoke:${rowKey(revokeTarget)}`]?.requestSubmit();
	}}
/>

<ConfirmDialog
	bind:open={suspendOpen}
	title="Suspend this person in the firm?"
	description={suspendTarget
		? `${data.user.name ?? data.user.email} will lose access to every portal in ${suspendTarget.tenantName}, and their active sessions will end. Restoring access returns all of those portals.`
		: undefined}
	confirmLabel="Suspend"
	destructive
	onconfirm={() => {
		if (suspendTarget) rowForms[`suspend:${suspendTarget.tenantUid}`]?.requestSubmit();
	}}
/>

<ConfirmDialog
	bind:open={mfaOpen}
	title="Reset two-factor authentication?"
	description={mfaTarget
		? `Removes every authenticator for ${data.user.name ?? data.user.email} in ${mfaTarget.tenantName} and ends their sessions. Verify their identity first. They must set up two-factor authentication again at their next sign-in.`
		: undefined}
	confirmLabel="Reset two-factor"
	destructive
	onconfirm={() => {
		if (mfaTarget) rowForms[`mfa:${mfaTarget.tenantUid}`]?.requestSubmit();
	}}
/>
