<script lang="ts" module>
	export type ActionCategory = { id: string; label: string };
	export type ActionProvider = { code: string; label: string };
	export type ActionPlanStep = { key: string; name: string; detail: string };
	export type ActionPlanGroup = {
		provider: string;
		label: string;
		steps: ActionPlanStep[];
	};
	export type ActionRow = {
		key: string;
		name: string;
		description: string;
		category: string;
		subject_kinds: string[];
		providers: ActionProvider[];
		reads?: string;
		plan?: ActionPlanGroup[] | null;
		kind?: string;
		holds_record?: boolean;
		enabled: boolean;
		approval_enabled: boolean;
		require_justification?: boolean;
		/** No provider API yet — the run halts for a person to accept, then completes by hand. */
		manual?: boolean;
		/** Which channels an approval notice uses. In-app on, email off by default. */
		notify_in_app?: boolean;
		notify_email?: boolean;
		approval_role_slugs: string[];
		role_slugs: string[];
	};
	export type ActionRole = { slug: string; name: string };
</script>

<script lang="ts">
	// One row per action, /team-style: Enabled, who may run it, who must approve it, and whether a
	// reason is asked — all edited in place, roles through the "+" picker. Onboarding actions keep
	// their sub-action breakdown behind an expander. Providers are tags, not a source picker: the
	// planner still reads the provider off the account.
	import { tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { Badge } from '../ui/badge/index.js';
	import RolePicker from '../ui/role-picker/role-picker.svelte';
	import type { RolePickerOption } from '../ui/role-picker/role-picker.svelte';
	import { SectionHeading } from '../ui/section-heading/index.js';
	import { Switch } from '../ui/switch/index.js';
	import type { Enhance } from '../book/clients/form-enhance.js';

	let {
		categories,
		actions,
		roles,
		enhance,
		canManage = true,
		selected = null,
		onSelect,
		form = null
	}: {
		categories: ActionCategory[];
		actions: ActionRow[];
		roles: ActionRole[];
		enhance: Enhance;
		canManage?: boolean;
		/** A deep-linked action (`?action=`): its sub-actions open on load so the link lands on them. */
		selected?: string | null;
		/** Expanding a row's sub-actions reports it back so the page can keep the deep link in step. */
		onSelect?: (key: string) => void;
		form?: { error?: string; action_key?: string } | null;
	} = $props();

	let saving = $state<string | null>(null);
	let enabledDraft = $state<Record<string, boolean>>({});
	let roleDraft = $state<Record<string, string[]>>({});
	let approvalDraft = $state<Record<string, string[]>>({});
	let justificationDraft = $state<Record<string, boolean>>({});
	let manualDraft = $state<Record<string, boolean>>({});
	let notifyInAppDraft = $state<Record<string, boolean>>({});
	let notifyEmailDraft = $state<Record<string, boolean>>({});
	// svelte-ignore state_referenced_locally
	let expanded = $state<Record<string, boolean>>(selected ? { [selected]: true } : {});

	const roleItems = $derived<RolePickerOption[]>(
		roles.map((role) => ({ value: role.slug, label: role.name }))
	);

	function enabledOf(row: ActionRow): boolean {
		return enabledDraft[row.key] ?? row.enabled;
	}
	function rolesOf(row: ActionRow): string[] {
		return roleDraft[row.key] ?? row.role_slugs;
	}
	function approversOf(row: ActionRow): string[] {
		return approvalDraft[row.key] ?? row.approval_role_slugs;
	}
	function isBook(row: ActionRow): boolean {
		return row.kind === 'book';
	}
	function recordHold(row: ActionRow): boolean {
		return isBook(row) || Boolean(row.holds_record);
	}
	function approvalOn(row: ActionRow): boolean {
		return approversOf(row).length > 0;
	}
	function requiresReason(row: ActionRow): boolean {
		return justificationDraft[row.key] ?? Boolean(row.require_justification);
	}
	function isManual(row: ActionRow): boolean {
		return manualDraft[row.key] ?? Boolean(row.manual);
	}
	// Approval-notice channels — default in-app on, email off.
	function notifyInApp(row: ActionRow): boolean {
		return notifyInAppDraft[row.key] ?? row.notify_in_app ?? true;
	}
	function notifyEmail(row: ActionRow): boolean {
		return notifyEmailDraft[row.key] ?? row.notify_email ?? false;
	}

	function formOf(key: string): HTMLFormElement | null {
		return document.querySelector(`form[data-action-row="${CSS.escape(key)}"]`);
	}

	// The hidden inputs are bound to the drafts, so a tick lets them flush before the form submits.
	async function submitRow(key: string): Promise<void> {
		await tick();
		formOf(key)?.requestSubmit();
	}

	function toggleExpand(key: string): void {
		expanded[key] = !expanded[key];
		if (expanded[key]) onSelect?.(key);
	}

	function watch(key: string) {
		saving = key;
		return async ({
			result,
			update
		}: {
			result: { type: string; data?: Record<string, unknown> };
			update: (opts?: { reset?: boolean }) => Promise<void>;
		}) => {
			saving = null;
			if (result.type === 'success') toast.success('Action updated');
			else {
				delete enabledDraft[key];
				delete justificationDraft[key];
				delete manualDraft[key];
				delete notifyInAppDraft[key];
				delete notifyEmailDraft[key];
				// Also drop the role drafts, or a refused 'Who can run'/'Approvers' change keeps
				// showing and the shared row form silently re-submits it on the next toggle.
				delete roleDraft[key];
				delete approvalDraft[key];
				toast.error(String(result.data?.error || 'Couldn’t update this action.'));
			}
			await update({ reset: false });
		};
	}
</script>

<div class="flex flex-col gap-6">
	{#each categories as category (category.id)}
		{@const rows = actions.filter((row) => row.category === category.id)}
		{#if rows.length}
			<section class="flex min-w-0 flex-col gap-2">
				<SectionHeading title={category.label} count={rows.length} />
				<div class="bg-card overflow-hidden rounded-xl border shadow-sm">
					<div class="overflow-x-auto">
						<div class="min-w-[960px]">
							<!-- header -->
							<div
								class="text-muted-foreground bg-muted/40 flex items-center gap-3 border-b px-3 py-2 text-[11px] font-semibold tracking-wide uppercase"
							>
								<span class="min-w-0 flex-1">Action</span>
								<span class="w-20 shrink-0 text-center">Enabled</span>
								<span class="w-48 shrink-0">Who can run</span>
								<span class="w-48 shrink-0">Approvers</span>
								<span class="w-16 shrink-0 text-center">Reason</span>
								<span class="w-16 shrink-0 text-center">Manual</span>
								<span class="w-28 shrink-0 text-center">Notify</span>
							</div>
							<div class="divide-border/60 divide-y">
								{#each rows as row (row.key)}
									<form
										method="POST"
										action="?/setPolicy"
										data-action-row={row.key}
										class="flex items-center gap-3 px-3 py-2"
										use:enhance={() => watch(row.key)}
									>
										<input type="hidden" name="action_key" value={row.key} />
										<input
											type="hidden"
											name="enabled"
											value={isBook(row) || enabledOf(row) ? 'true' : 'false'}
										/>
										<input
											type="hidden"
											name="approval_enabled"
											value={approvalOn(row) ? 'true' : 'false'}
										/>
										<input
											type="hidden"
											name="require_justification"
											value={requiresReason(row) ? 'true' : 'false'}
										/>
										<input type="hidden" name="manual" value={isManual(row) ? 'true' : 'false'} />
										<input
											type="hidden"
											name="notify_in_app"
											value={notifyInApp(row) ? 'true' : 'false'}
										/>
										<input
											type="hidden"
											name="notify_email"
											value={notifyEmail(row) ? 'true' : 'false'}
										/>
										{#each rolesOf(row) as slug (slug)}
											<input type="hidden" name="role_slugs" value={slug} />
										{/each}
										{#each approversOf(row) as slug (slug)}
											<input type="hidden" name="approval_role_slugs" value={slug} />
										{/each}

										<!-- Action -->
										<div class="flex min-w-0 flex-1 items-start gap-1.5">
											{#if row.plan?.length}
												<button
													type="button"
													class="text-muted-foreground hover:text-foreground mt-0.5 shrink-0"
													aria-label={expanded[row.key] ? 'Hide sub-actions' : 'Show sub-actions'}
													aria-expanded={expanded[row.key] ?? false}
													onclick={() => toggleExpand(row.key)}
												>
													<ChevronRightIcon
														class="size-3.5 transition-transform {expanded[row.key]
															? 'rotate-90'
															: ''}"
													/>
												</button>
											{:else}
												<span class="w-3.5 shrink-0"></span>
											{/if}
											<div class="min-w-0">
												<span class="block text-xs font-medium" title={row.description}
													>{row.name}</span
												>
												{#if row.providers.length}
													<span class="mt-1 flex flex-wrap gap-1">
														{#each row.providers as provider (provider.code)}
															<Badge variant="outline">{provider.label}</Badge>
														{/each}
													</span>
												{/if}
												{#if form?.error && form.action_key === row.key}
													<span class="text-destructive mt-1 block text-[11px]">{form.error}</span>
												{/if}
											</div>
										</div>

										<!-- Enabled -->
										<div class="flex w-20 shrink-0 justify-center">
											{#if isBook(row)}
												<span class="text-muted-foreground text-[11px]">Always</span>
											{:else}
												<Switch
													checked={enabledOf(row)}
													disabled={!canManage || saving === row.key}
													aria-label={`Enable ${row.name}`}
													onCheckedChange={async (checked) => {
														enabledDraft[row.key] = checked;
														await submitRow(row.key);
													}}
												/>
											{/if}
										</div>

										<!-- Who can run -->
										<div class="w-48 shrink-0">
											{#if isBook(row)}
												<span class="text-muted-foreground text-xs">—</span>
											{:else}
												<RolePicker
													options={roleItems}
													value={rolesOf(row)}
													allowEmpty
													placeholder="Anyone"
													disabled={!canManage || saving === row.key || !enabledOf(row)}
													ariaLabel={`Roles that may run ${row.name}`}
													onchange={(next) => {
														roleDraft[row.key] = next;
														void submitRow(row.key);
													}}
												/>
											{/if}
										</div>

										<!-- Approvers -->
										<div class="w-48 shrink-0">
											<RolePicker
												options={roleItems}
												value={approversOf(row)}
												allowEmpty
												placeholder="No approval"
												disabled={!canManage ||
													saving === row.key ||
													(!recordHold(row) && !enabledOf(row))}
												ariaLabel={`Roles that may approve ${row.name}`}
												onchange={(next) => {
													approvalDraft[row.key] = next;
													void submitRow(row.key);
												}}
											/>
										</div>

										<!-- Reason -->
										<div class="flex w-16 shrink-0 justify-center">
											<Switch
												checked={requiresReason(row)}
												disabled={!canManage ||
													saving === row.key ||
													!approvalOn(row) ||
													(!recordHold(row) && !enabledOf(row))}
												aria-label={`Require a reason for ${row.name}`}
												onCheckedChange={async (checked) => {
													justificationDraft[row.key] = checked;
													await submitRow(row.key);
												}}
											/>
										</div>

										<!-- Manual (halt for a person; no provider API yet) -->
										<div class="flex w-16 shrink-0 justify-center">
											{#if isBook(row)}
												<span class="text-muted-foreground text-xs">—</span>
											{:else}
												<Switch
													checked={isManual(row)}
													disabled={!canManage || saving === row.key || !enabledOf(row)}
													aria-label={`Complete ${row.name} manually`}
													onCheckedChange={async (checked) => {
														manualDraft[row.key] = checked;
														await submitRow(row.key);
													}}
												/>
											{/if}
										</div>

										<!-- Notify: which channels an approval notice uses. Only with an approver set. -->
										<div class="flex w-28 shrink-0 items-center justify-center gap-3">
											{#if approvalOn(row)}
												<label
													class="text-muted-foreground flex items-center gap-1 text-[11px]"
													title="In-app notification"
												>
													<Switch
														checked={notifyInApp(row)}
														disabled={!canManage || saving === row.key}
														aria-label={`In-app approval notice for ${row.name}`}
														onCheckedChange={async (checked) => {
															notifyInAppDraft[row.key] = checked;
															await submitRow(row.key);
														}}
													/>
													App
												</label>
												<label
													class="text-muted-foreground flex items-center gap-1 text-[11px]"
													title="Email"
												>
													<Switch
														checked={notifyEmail(row)}
														disabled={!canManage || saving === row.key}
														aria-label={`Email approval notice for ${row.name}`}
														onCheckedChange={async (checked) => {
															notifyEmailDraft[row.key] = checked;
															await submitRow(row.key);
														}}
													/>
													Email
												</label>
											{:else}
												<span class="text-muted-foreground text-xs">—</span>
											{/if}
										</div>
									</form>

									{#if expanded[row.key] && row.plan?.length}
										<div class="bg-muted/20 flex flex-col gap-3 px-3 py-3 pl-8">
											{#if row.description}
												<p class="text-muted-foreground text-xs">{row.description}</p>
											{/if}
											{#each row.plan as group (group.provider)}
												<section class="flex flex-col gap-1">
													<h3 class="text-xs font-medium">{group.label}</h3>
													<ol class="flex flex-col gap-1.5">
														{#each group.steps as step, index (step.key)}
															<li class="flex gap-2">
																<span
																	class="text-muted-foreground w-4 shrink-0 text-xs tabular-nums"
																	>{index + 1}</span
																>
																<span class="min-w-0">
																	<span class="block text-xs font-medium">{step.name}</span>
																	<span class="text-muted-foreground block text-xs"
																		>{step.detail}</span
																	>
																</span>
															</li>
														{/each}
													</ol>
												</section>
											{/each}
										</div>
									{/if}
								{/each}
							</div>
						</div>
					</div>
				</div>
			</section>
		{/if}
	{/each}
</div>
