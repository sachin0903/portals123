<script lang="ts" module>
	/** The portals a role can belong to. Structural — the platform app's narrower
	 *  `'client' | 'platform'` is assignable. */
	export type RolesEditorPortal = 'platform' | 'client' | 'operations';

	/** A role as the editor reads it (each app's richer API type is assignable). */
	export type RolesEditorRole = {
		slug: string;
		name: string;
		description: string | null;
		portal: RolesEditorPortal | null;
		permissions: string[];
	};

	/** A grantable permission as the editor reads it. */
	export type RolesEditorPermission = {
		slug: string;
		name: string;
		resource: string | null;
		portal: RolesEditorPortal | null;
		description: string | null;
	};
</script>

<script lang="ts">
	// The roles & permissions editor — one implementation for every surface that manages roles
	// (the platform portal's /team/roles, and the admin portal's tenant Roles, Model tenant and
	// Internal Operations pages). It used to live as a near-verbatim copy in BOTH apps; the
	// UI-consolidation sweep hoisted the superset (the admin variant, which had grown the
	// operations portal, `update({ reset: false })` and the aria-labels) here.
	//
	// It posts to the HOST page's `?/createRole` and `?/setRolePermissions` actions, with the
	// app's own `enhance` injected as a prop — pkgs/ui cannot import `$app/forms` (SvelteKit's
	// virtuals only exist inside an app; see book/clients/form-enhance.ts).
	import SearchIcon from '@lucide/svelte/icons/search';
	import ShieldIcon from '@lucide/svelte/icons/shield-check';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import { toast } from 'svelte-sonner';
	import { Alert, AlertDescription } from '../ui/alert/index.js';
	import { Badge } from '../ui/badge/index.js';
	import { Button } from '../ui/button/index.js';
	import { Card } from '../ui/card/index.js';
	import * as Dialog from '../ui/dialog/index.js';
	import EmptyState from '../ui/empty-state/empty-state.svelte';
	import { Input } from '../ui/input/index.js';
	import { Label } from '../ui/label/index.js';
	import { SectionHeading } from '../ui/section-heading/index.js';
	import { default as Segmented } from '../ui/segmented/segmented.svelte';
	import { Switch } from '../ui/switch/index.js';
	import { cn } from '../../utils.js';
	import type { Enhance } from '../book/clients/form-enhance.js';

	const ALL_PORTALS: { id: RolesEditorPortal; label: string; hint: string }[] = [
		{ id: 'platform', label: 'Platform', hint: 'Firm staff — advisers, paraplanners, compliance' },
		{ id: 'client', label: 'Client portal', hint: 'End clients' },
		{ id: 'operations', label: 'Internal Operations', hint: 'Graphene ops staff' }
	];

	// How each portal reads in copy ("No … roles yet", "New … role").
	const PORTAL_NOUN: Record<string, string> = {
		platform: 'platform',
		client: 'client-portal',
		operations: 'operations'
	};

	let {
		roles,
		permissions,
		form,
		enhance,
		// Which portals this editor exposes. Defaults to the firm portals; the Internal
		// Operations editor passes just ['operations'] (then the segmented control is hidden).
		portals = ['platform', 'client'],
		// Whether to surface the mutating controls (create + save). The API enforces write
		// regardless; this only hides the controls from viewers. Defaults to true.
		canManage = true
	}: {
		roles: RolesEditorRole[];
		permissions: RolesEditorPermission[];
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		form: any;
		/** The app's own `enhance` from `$app/forms` — injected, not imported (see above). */
		enhance: Enhance;
		portals?: RolesEditorPortal[];
		canManage?: boolean;
	} = $props();

	const PORTALS = $derived(ALL_PORTALS.filter((p) => portals.includes(p.id)));

	// Selected portal: null => the first available (derived, so it tracks the `portals` prop).
	// The trailing 'platform' only satisfies noUncheckedIndexedAccess — `portals` is never empty.
	let portalSel = $state<RolesEditorPortal | null>(null);
	const portal = $derived<RolesEditorPortal>(portalSel ?? portals[0] ?? 'platform');
	const portalNoun = $derived(PORTAL_NOUN[portal] ?? portal);
	let selectedSlug = $state<string | null>(null);
	let search = $state('');
	let draft = $state<Set<string>>(new Set());
	let createOpen = $state(false);
	let saving = $state(false);
	let creating = $state(false);

	const portalRoles = $derived(roles.filter((r) => r.portal === portal));
	const portalPerms = $derived(permissions.filter((p) => p.portal === portal));
	const selectedRole = $derived(
		portalRoles.find((r) => r.slug === selectedSlug) ?? portalRoles[0] ?? null
	);

	$effect(() => {
		draft = new Set(selectedRole?.permissions ?? []);
	});

	const groups = $derived.by(() => {
		const q = search.trim().toLowerCase();
		const matched = portalPerms.filter(
			(p) => !q || `${p.name} ${p.slug} ${p.description ?? ''}`.toLowerCase().includes(q)
		);
		const by = new Map<string, typeof matched>();
		for (const p of matched) {
			const key = p.resource ?? 'other';
			(by.get(key) ?? by.set(key, []).get(key)!).push(p);
		}
		return [...by.entries()].sort(([a], [b]) => a.localeCompare(b));
	});

	// Operations-only grants (including `system`) can sit on a Graphene platform role because
	// the operations tenant is allowed to assign them. They have no switch on this tab, and
	// they do not apply to a firm-portal session — say so, or turning Create clients off
	// looks like it did nothing.
	const hiddenOperations = $derived(
		portal === 'operations' || !selectedRole
			? []
			: selectedRole.permissions.filter((s) =>
					permissions.some((p) => p.slug === s && p.portal === 'operations')
				)
	);
	// Fall back to the stored grant: `draft` is seeded in an $effect, which does not run on
	// the server, so a role that holds system would otherwise render no warning until hydrate.
	const carriesSystem = $derived(
		portal === 'operations' &&
			(draft.has('system') || Boolean(selectedRole?.permissions.includes('system')))
	);

	const dirty = $derived.by(() => {
		if (!selectedRole) return false;
		const saved = new Set(selectedRole.permissions);
		if (saved.size !== draft.size) return true;
		for (const s of saved) if (!draft.has(s)) return true;
		return false;
	});

	function toggle(slug: string) {
		const next = new Set(draft);
		if (next.has(slug)) next.delete(slug);
		else next.add(slug);
		draft = next;
	}

	function pickPortal(p: RolesEditorPortal) {
		portalSel = p;
		selectedSlug = null;
	}
</script>

<!-- Fills the visible area so the save bar can sit at the bottom of the content column
	 (mt-auto) rather than floating mid-page after short content. -->
<div class="flex min-h-[calc(100svh-9rem)] flex-col">
	<!-- Portal segmented control + create -->
	<div class="mb-6 flex items-center justify-between gap-3">
		{#if PORTALS.length > 1}
			<Segmented
				options={PORTALS.map((p) => ({ value: p.id, label: p.label }))}
				value={portal}
				onValueChange={(v) => pickPortal(v as RolesEditorPortal)}
			/>
		{:else}
			<div></div>
		{/if}
		{#if canManage}
			<Button size="sm" onclick={() => (createOpen = true)}>
				<PlusIcon class="size-4" /> New role
			</Button>
		{/if}
	</div>

	<div class="grid gap-6 md:grid-cols-[16rem_1fr]">
		<!-- Role rail -->
		<div class="space-y-1">
			{#if portalRoles.length === 0}
				{#if canManage}
					<EmptyState compact title="No {portalNoun} roles yet">
						{#snippet action()}
							<Button variant="link" size="sm" onclick={() => (createOpen = true)}>
								Create one
							</Button>
						{/snippet}
					</EmptyState>
				{:else}
					<EmptyState compact title="No {portalNoun} roles yet" />
				{/if}
			{:else}
				{#each portalRoles as role (role.slug)}
					<Button
						type="button"
						variant="ghost"
						onclick={() => (selectedSlug = role.slug)}
						class={cn(
							'w-full justify-start gap-2.5 px-3 text-xs',
							selectedRole?.slug === role.slug
								? 'bg-accent text-accent-foreground hover:bg-accent'
								: 'text-muted-foreground'
						)}
					>
						<ShieldIcon class="size-4 shrink-0 opacity-70" />
						<span class="min-w-0 flex-1 truncate text-left font-medium">{role.name}</span>
						<span class="text-muted-foreground text-xs tabular-nums">{role.permissions.length}</span
						>
					</Button>
				{/each}
			{/if}
		</div>

		<!-- Permission editor -->
		<div class="min-w-0">
			{#if !selectedRole}
				<EmptyState
					title="Select or create a role"
					description="Pick a role on the left to edit its permissions."
				/>
			{:else}
				<div class="mb-4 flex flex-wrap items-center justify-between gap-2">
					<div>
						<h2 class="text-lg font-semibold tracking-tight">{selectedRole.name}</h2>
						{#if selectedRole.description}
							<p class="text-muted-foreground text-xs">{selectedRole.description}</p>
						{/if}
					</div>
					<Badge variant="outline" class="font-mono">{selectedRole.slug}</Badge>
				</div>

				{#if hiddenOperations.length}
					<Alert variant="info" class="mb-4">
						<AlertDescription>
							This role also holds Internal Operations permissions{hiddenOperations.includes(
								'system'
							)
								? ', including System administration,'
								: ''} that do not appear here. Those apply only when signed in to Internal Operations;
							on this portal the switches below are what the person can do.
						</AlertDescription>
					</Alert>
				{/if}
				{#if carriesSystem}
					<Alert variant="warning" class="mb-4">
						<AlertDescription>
							System administration bypasses every other permission on this portal. Turning a switch
							off does not constrain a person who holds it.
						</AlertDescription>
					</Alert>
				{/if}

				{#if portalPerms.length === 0}
					<EmptyState title="No {portalNoun} permissions defined yet" />
				{:else}
					<div class="relative mb-4 max-w-sm">
						<SearchIcon
							class="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2"
						/>
						<Input bind:value={search} placeholder="Search permissions…" class="pl-9" />
					</div>

					<div class="space-y-6">
						{#each groups as [resource, perms] (resource)}
							<section>
								<SectionHeading title={resource} count={perms.length} />
								<Card glass class="divide-border/60 gap-0 divide-y py-0">
									{#each perms as perm (perm.slug)}
										<label class="flex items-center justify-between gap-4 px-4 py-3">
											<div class="min-w-0">
												<div class="text-xs font-medium">{perm.name}</div>
												<div class="text-muted-foreground text-xs">
													{perm.description ?? perm.slug}
												</div>
											</div>
											<Switch
												checked={draft.has(perm.slug)}
												disabled={!canManage}
												aria-label={perm.name}
												onCheckedChange={() => toggle(perm.slug)}
											/>
										</label>
									{/each}
								</Card>
							</section>
						{/each}
					</div>
				{/if}

				{#if form?.action === 'setRolePermissions' && 'error' in form}
					<Alert variant="destructive" class="mt-4">
						<AlertDescription>{form.error}</AlertDescription>
					</Alert>
				{/if}
			{/if}
		</div>
	</div>

	<!-- Save bar (appears when the draft differs from what's saved) -->
	{#if selectedRole && dirty && canManage}
		<div
			class="glass-bar sticky bottom-0 z-20 -mx-4 -mb-4 mt-auto flex items-center justify-between gap-3 border-t px-4 py-3 md:-mx-6 md:-mb-6 md:px-6"
		>
			<span class="text-xs">Unsaved changes to <strong>{selectedRole.name}</strong></span>
			<form
				method="POST"
				action="?/setRolePermissions"
				use:enhance={() => {
					saving = true;
					return async ({ result, update }) => {
						saving = false;
						if (result.type === 'success') toast.success('Permissions saved.');
						await update({ reset: false });
					};
				}}
				class="flex items-center gap-2"
			>
				<input type="hidden" name="slug" value={selectedRole.slug} />
				{#each [...draft] as slug (slug)}
					<input type="hidden" name="permissions" value={slug} />
				{/each}
				<Button
					type="button"
					variant="ghost"
					size="sm"
					disabled={saving}
					onclick={() => (draft = new Set(selectedRole.permissions))}
				>
					Reset
				</Button>
				<Button type="submit" size="sm" disabled={saving}>
					{saving ? 'Saving…' : 'Save changes'}
				</Button>
			</form>
		</div>
	{/if}
</div>

<!-- Create-role dialog -->
<Dialog.Root bind:open={createOpen}>
	<Dialog.Content>
		<Dialog.Header>
			<Dialog.Title>New {portalNoun} role</Dialog.Title>
			<Dialog.Description>
				Create a role here. Toggle its permissions after it's created.
			</Dialog.Description>
		</Dialog.Header>
		<form
			method="POST"
			action="?/createRole"
			use:enhance={() => {
				creating = true;
				return async ({ result, update }) => {
					creating = false;
					if (result.type === 'success') {
						createOpen = false;
						toast.success('Role created.');
					}
					await update();
				};
			}}
			class="space-y-4"
		>
			<input type="hidden" name="portal" value={portal} />
			<div class="space-y-1">
				<Label for="role-name">Name</Label>
				<Input id="role-name" name="name" placeholder="Senior adviser" />
			</div>
			<div class="space-y-1">
				<Label for="role-slug">Slug</Label>
				<Input
					id="role-slug"
					name="slug"
					placeholder="senior-adviser"
					pattern="[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?"
				/>
				<p class="text-muted-foreground text-xs">Lowercase letters, numbers, hyphens.</p>
			</div>
			{#if form?.action === 'createRole' && 'error' in form}
				<p class="text-destructive text-xs">{form.error}</p>
			{/if}
			<Dialog.Footer>
				<Button type="button" variant="ghost" onclick={() => (createOpen = false)}>Cancel</Button>
				<Button type="submit" disabled={creating}>
					{creating ? 'Creating…' : 'Create role'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
