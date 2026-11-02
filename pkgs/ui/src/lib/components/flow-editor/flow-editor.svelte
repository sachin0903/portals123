<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import type { Enhance, EnhanceOutcome } from '../book/clients/form-enhance.js';
	import {
		Alert,
		AlertDescription,
		Button,
		ConfirmDialog,
		Sheet,
		StatusBadge,
		Badge
	} from '@graphene/ui';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import CheckIcon from '@lucide/svelte/icons/check';
	import CheckCircleIcon from '@lucide/svelte/icons/check-circle-2';
	import FlaskConicalIcon from '@lucide/svelte/icons/flask-conical';
	import ChartNoAxesColumnIcon from '@lucide/svelte/icons/chart-no-axes-column';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import { OFFERED_ACTION_TYPES, isActionAvailable } from '@graphene/ui/automations';
	import {
		branchesOf,
		deleteNode,
		flowStatusTone,
		isGraphDefinition,
		isLegacyShaped,
		repairTargetsFor,
		toDefinition,
		toNodes,
		intakeOf,
		FlowDryRun,
		type FlowMilestone,
		type FlowNode,
		type FlowIntake,
		type FlowSimulation
	} from '@graphene/ui/book/journeys';
	import {
		FlowCanvas,
		FlowInspectorPanel as InspectorPanel,
		FlowIntakeInspector as IntakeInspector,
		FlowTriggerPanel as TriggerPanel,
		INTAKE_ID,
		createValidationRunner,
		groupIssues,
		NO_ISSUES,
		type FlowIssue
	} from '@graphene/ui/flow-editor';
	import { humanise } from '../../sentence-case.js';
	type FlowEditorData = {
		flow: import('@graphene/ui/book/journeys').FlowOut | null;
		version: import('@graphene/ui/book/journeys').FlowVersionOut | null;
		forms: import('@graphene/ui/book/journeys').FlowForm[];
		documents: import('@graphene/ui/book/journeys').FlowDocument[];
		templates: import('@graphene/ui/book/journeys').FlowTemplate[];
		terms?: { uid: string; title: string }[];
		products: import('@graphene/ui/book/journeys').CatalogProduct[];
		roles: { slug: string; name: string }[];
		flows: { uid: string; code: string; name: string }[];
		dryRunClients: import('@graphene/ui/book/journeys').DryRunClient[];
		canManage: boolean;
	};
	type Navigation = { cancel: () => void; to?: { url: URL } | null };
	type BeforeNavigate = (callback: (navigation: Navigation) => void) => void;
	type Navigate = (url: string | URL) => Promise<void>;
	let {
		data,
		form,
		enhance,
		beforeNavigate,
		navigate,
		invalidate,
		links,
		endpoints
	}: {
		data: FlowEditorData;
		form: Record<string, unknown> | null | undefined;
		enhance: Enhance;
		beforeNavigate: BeforeNavigate;
		navigate: Navigate;
		invalidate: () => Promise<void>;
		/** `export` is the same-origin download of the flow as a file (`docs/flows.md`, "Export and
		 *  import"); a portal that has no proxy for it leaves it out and the button is not drawn. */
		links: { index: string; insights: (uid: string) => string; export?: (uid: string) => string };
		endpoints: { validate: string; triggers: string; formFields: string };
	} = $props();

	const flow = $derived(data.flow);

	// The authored GRAPH is the source of truth; the canvas is a projection of it. A legacy
	// `{steps}` definition is read as nodes in memory (`toNodes`) and only written back as `{nodes}`
	// when the author saves — opening a published journey must not rewrite it.
	// untrack: seed once from the latest version, don't resync.
	const initDef = untrack(() => data.version?.definition ?? { steps: [], milestones: [] });
	const lookups = untrack(() => ({
		forms: data.forms,
		documents: data.documents,
		templates: data.templates
	}));
	const wasLegacy = untrack(() => !!data.version && !isGraphDefinition(initDef));
	let nodes = $state<FlowNode[]>(untrack(() => toNodes(structuredClone(initDef))));
	let milestones = $state<FlowMilestone[]>(
		untrack(() => structuredClone(initDef.milestones ?? []))
	);
	let intake = $state<FlowIntake>(untrack(() => intakeOf(structuredClone(initDef.intake ?? null))));
	let selectedId = $state<string | null>(null);

	/**
	 * Publish issues from the engine, grouped by node — the badges, the count and every inline
	 * message in the inspector all read from here.
	 *
	 * They come from the ENGINE rather than from a second opinion implemented in TypeScript, which
	 * is the point: `graph_issues` is what publish will actually refuse on, so a builder that
	 * predicted refusals itself would eventually disagree with the thing doing the refusing. The
	 * runner debounces and drops out-of-order answers (`validation.ts`).
	 */
	let rawIssues = $state<FlowIssue[]>([]);
	let validating = $state(false);
	const issues = $derived(
		rawIssues.length
			? groupIssues(
					rawIssues,
					nodes.map((n) => n.id)
				)
			: NO_ISSUES
	);

	/** Only the actions the engine can actually run. `isActionAvailable` reads the shared parked set
	 *  rather than naming a type here, so the palette and the action inspector cannot disagree and
	 *  neither needs editing when the next action ships its handler. */
	const actionTypes = OFFERED_ACTION_TYPES.filter((a) => isActionAvailable(a.type)).map((a) => ({
		type: a.type,
		label: a.label
	}));

	let dirty = $state(false);
	let saveState = $state<'idle' | 'saving' | 'saved' | 'error'>('idle');
	let publishing = $state(false);
	let error = $state<string | null>(null);

	function markDirty() {
		dirty = true;
		if (saveState !== 'idle') saveState = 'idle';
	}
	/** The graph's shape changed. The canvas re-projects on its own (it watches the document), so
	 *  this is purely "the draft is now different from what is saved". */
	function restructure() {
		markDirty();
	}

	/** Still expressible in the old shape — which is also what decides which document a save writes
	 *  (`toDefinition`). Derived rather than latched, so the notice below clears itself the moment
	 *  the author adds the branch that converts the journey. */
	const stillLegacy = $derived(isLegacyShaped(nodes));
	const showIntake = $derived(selectedId === INTAKE_ID);
	const selectedNode = $derived(nodes.find((n) => n.id === selectedId) ?? null);
	/** The draft as it would be POSTed. One derivation feeds the save/publish hidden inputs, the
	 *  live validator and the dry run, so all three are always talking about the same document. */
	const definition = $derived(toDefinition(nodes, milestones, intake));
	const definitionJson = $derived(JSON.stringify(definition));
	const canPublish = $derived(nodes.length > 0 && nodes.every((n) => n.title.trim().length > 0));

	// ── Deleting, with the one case that needs asking ─────────────────────────────────────────────
	let deleteOpen = $state(false);
	let doomed = $state<string[]>([]);

	function requestDelete(ids: string[]) {
		doomed = ids;
		deleteOpen = true;
	}
	function confirmDelete() {
		let next = nodes;
		for (const id of doomed) next = deleteNode(next, id);
		nodes = next;
		if (selectedId && doomed.includes(selectedId)) selectedId = null;
		doomed = [];
		markDirty();
	}

	/**
	 * What deleting these nodes will do to the wiring, said out loud.
	 *
	 * A chain repairs itself silently and does not need explaining. A BRANCH does: its arms lead
	 * several places and only one of them can inherit its predecessors, so the author is told which
	 * — deleting a fork and finding the journey now always takes the path you were trying to remove
	 * is not something to discover afterwards.
	 */
	const deleteDescription = $derived.by(() => {
		const branch = doomed
			.map((id) => nodes.find((n) => n.id === id))
			.find((n) => n?.kind === 'branch');
		if (branch) {
			const heir = repairTargetsFor(nodes, branch.id)[0] ?? null;
			const target = heir ? (nodes.find((n) => n.id === heir)?.title ?? heir) : null;
			const arms = branchesOf(branch).length;
			return target
				? `“${branch.title || branch.id}” has ${arms} paths and they cannot all be kept. ` +
						`Everything that led to the branch will lead to “${target}” instead — the “otherwise” ` +
						'path. Nothing changes until you save.'
				: `“${branch.title || branch.id}” has ${arms} paths and none of them lead anywhere yet, ` +
						'so whatever led to the branch will lead nowhere. Nothing changes until you save.';
		}
		return doomed.length > 1
			? `${doomed.length} nodes are removed from the draft and the wiring around them is joined up. Nothing changes until you save.`
			: 'The node is removed from the draft and the wiring around it is joined up. Nothing changes until you save.';
	});

	function onSaveResult(result: EnhanceOutcome) {
		saveState = 'idle';
		publishing = false;
		if (result.type === 'success') {
			dirty = false;
			saveState = result.data?.published ? 'idle' : 'saved';
			error = null;
			invalidate();
		} else if (result.type === 'failure') {
			saveState = 'error';
			error = (result.data?.error as string) ?? 'Could not save the flow.';
		}
	}

	// Unsaved-changes guard: cancel the navigation, confirm in a dialog, then resume it.
	let leaveOpen = $state(false);
	let pendingHref: string | null = null;
	let leaveConfirmed = false;
	untrack(() => beforeNavigate)((nav) => {
		if (!dirty || leaveConfirmed) return;
		nav.cancel();
		pendingHref = nav.to?.url.href ?? null;
		leaveOpen = true;
	});

	/**
	 * Live validation. Created once (the page is per-flow) and fed the draft on every change; the
	 * runner owns the debounce and the out-of-order guard. Null only on the not-found branch, where
	 * there is no flow to validate.
	 */
	const validator = untrack(() =>
		data.flow
			? createValidationRunner(data.flow.uid, (issues) => (rawIssues = issues), {
					endpoint: endpoints.validate,
					onpending: (pending) => (validating = pending)
				})
			: null
	);
	$effect(() => {
		validator?.request(definition);
	});
	onDestroy(() => validator?.dispose());

	// ── The dry run, in a drawer ──────────────────────────────────────────────────────────────────
	// It lives in the builder because the thing worth testing is the graph ON SCREEN, not the last
	// version somebody saved. `runDryRun` is shared with the standalone /dry-run page so the two
	// mounts cannot behave differently, and the API endpoint performs nothing.
	let dryRunOpen = $state(false);
	let dryRunPending = $state(false);
	const dryRunResult = $derived(
		form && 'dryRun' in form ? ((form.dryRun ?? null) as FlowSimulation | null) : null
	);
	const dryRunError = $derived(
		form && 'dryRunError' in form ? ((form.dryRunError ?? null) as string | null) : null
	);

	/** The panel's form, submitted with the CURRENT draft attached.
	 *
	 *  The definition rides on `formData` rather than on a hidden input inside the shared panel: the
	 *  panel belongs to pkgs/ui and has no slot for host fields, and setting it here means the body
	 *  carries whatever is on the canvas at the instant of submission rather than whatever was
	 *  rendered when the drawer opened. `reset: false` keeps the chosen subject so a second run is
	 *  one click. */
	const dryRunSubmit = (node: HTMLFormElement) =>
		enhance(node, ({ formData }) => {
			formData.set('definition', definitionJson);
			dryRunPending = true;
			return async ({ update }) => {
				dryRunPending = false;
				await update({ reset: false });
			};
		});

	const statusLine = $derived(
		saveState === 'saving'
			? 'Saving…'
			: saveState === 'error'
				? (error ?? 'Save failed — try again.')
				: dirty
					? 'Unsaved changes.'
					: saveState === 'saved'
						? 'Draft saved.'
						: 'Changes are kept until you save.'
	);
</script>

{#if !flow}
	<div class="mx-auto max-w-3xl p-6">
		<p class="text-muted-foreground text-sm">This flow could not be found.</p>
		<Button variant="outline" class="mt-4" href={links.index}>
			<ArrowLeftIcon class="mr-1.5 size-4" /> Back to flows
		</Button>
	</div>
{:else}
	<div class="flex h-[calc(100dvh-var(--topbar-h,4rem))] flex-col">
		<!-- Header -->
		<div class="flex items-center gap-4 border-b px-5 py-2.5">
			<Button variant="ghost" size="sm" href={links.index}>
				<ArrowLeftIcon class="mr-1.5 size-4" /> Flows
			</Button>
			<div class="flex min-w-0 items-center gap-2">
				<h1 class="truncate text-lg font-semibold">{flow.name}</h1>
				<StatusBadge tone={flowStatusTone(flow.status)}>
					{humanise(flow.status)}
				</StatusBadge>
				{#if flow.published_version != null}
					<Badge variant="outline">v{flow.published_version}</Badge>
				{/if}
				{#if issues.all.length}
					<Badge variant="destructive">
						{issues.all.length}
						{issues.all.length === 1 ? 'problem' : 'problems'}
					</Badge>
				{:else if validating}
					<!-- Only while there is nothing to report: replacing a live count with "Checking…" on
					     every keystroke would make the one number an author is watching flicker. -->
					<span class="text-muted-foreground text-xs">Checking…</span>
				{/if}
			</div>
			<!-- `min-w-0` + no-wrap: this sits in a flex row between the status badges and the action
			     buttons, and without it the sentence was squeezed into a three-line column between
			     "v1" and "Insights". It is a status line, so it truncates rather than reflowing the
			     header around it. -->
			<span
				class="min-w-0 truncate text-sm whitespace-nowrap"
				class:text-muted-foreground={!dirty && saveState !== 'error'}
				class:text-warning={dirty && saveState !== 'error'}
				class:text-destructive={saveState === 'error'}
			>
				{statusLine}
			</span>
			<div class="ml-auto flex shrink-0 items-center gap-2">
				<!-- C3's two read surfaces, reachable from the thing they are about. Insights is a page
				     (it is a report on every case that ever ran); the dry run is a drawer, because its
				     whole value is testing the graph you are looking at without leaving it. -->
				<Button variant="ghost" size="sm" href={links.insights(flow.uid)}>
					<ChartNoAxesColumnIcon class="mr-1.5 size-4" /> Insights
				</Button>
				{#if links.export}
					<!-- The published definition with the forms and templates it names, as one file.
					     `download` keeps the router off the link: the proxy answers with an attachment. -->
					<Button
						variant="ghost"
						size="sm"
						href={links.export(flow.uid)}
						download
						data-sveltekit-reload
						aria-label="Export this flow as a file"
					>
						<DownloadIcon class="mr-1.5 size-4" /> Export
					</Button>
				{/if}
				{#if data.canManage}
					<Button variant="outline" size="sm" onclick={() => (dryRunOpen = true)}>
						<FlaskConicalIcon class="mr-1.5 size-4" /> Dry run
					</Button>
				{/if}
			</div>
			{#if data.canManage}
				<div class="flex shrink-0 items-center gap-2">
					<form
						method="POST"
						action="?/save"
						use:enhance={() => {
							saveState = 'saving';
							error = null;
							return async ({ result }) => onSaveResult(result);
						}}
					>
						<input type="hidden" name="definition" value={definitionJson} />
						<Button
							type="submit"
							variant="outline"
							size="sm"
							disabled={!dirty || saveState === 'saving'}
						>
							<CheckIcon class="mr-1.5 size-4" />
							{saveState === 'saving' ? 'Saving…' : 'Save draft'}
						</Button>
					</form>
					<form
						method="POST"
						action="?/publish"
						use:enhance={async () => {
							publishing = true;
							error = null;
							// Flush before the request goes: `enhance` awaits this callback, so the badges
							// on screen describe the draft being published rather than the one before the
							// last keystroke. The server refuses regardless — this is about the author
							// seeing WHY before the refusal arrives.
							await validator?.flush(definition);
							return async ({ result }) => onSaveResult(result);
						}}
					>
						<input type="hidden" name="definition" value={definitionJson} />
						<Button type="submit" size="sm" disabled={!canPublish || publishing}>
							<CheckCircleIcon class="mr-1.5 size-4" />
							{publishing ? 'Publishing…' : 'Publish'}
						</Button>
					</form>
				</div>
			{/if}
		</div>

		<!-- "Starts when…" sits above the canvas: what starts a journey is the first thing an author
		     asks about it, and it is a rule rather than a node, so it is chrome and not a block on the
		     graph. Capped in height so a firm with four triggers cannot squeeze the canvas out. -->
		<div class="max-h-64 shrink-0 overflow-y-auto px-5 pt-3">
			<TriggerPanel
				flowUid={flow.uid}
				flowName={flow.name}
				canManage={data.canManage}
				endpoint={endpoints.triggers}
			/>
		</div>

		{#if error}
			<Alert variant="destructive" class="mx-5 mt-3 w-auto">
				<AlertDescription>{error}</AlertDescription>
			</Alert>
		{/if}

		{#if wasLegacy && stillLegacy && data.canManage}
			<!--
				Not a warning — an explanation of what the canvas is showing. A journey built in the old
				editor has no wires, because a legacy definition never had any: its order was
				`depends_on`, and the sequence the old canvas drew between steps was inferred at render
				time and stored nowhere. So the arrows here are the prerequisites and only the
				prerequisites, saving changes nothing at all, and the format moves only when the author
				reaches for something the old shape cannot express.
			-->
			<Alert class="mx-5 mt-3 w-auto">
				<AlertDescription>
					This journey was built in the old editor, where steps were ordered by their
					<em>“Runs after”</em> prerequisites — the “after” arrows — rather than by wires. Saving leaves
					it exactly as it is. Adding a branch, an action or a wire converts it to the new graph format,
					and from then on the wires decide the order.
				</AlertDescription>
			</Alert>
		{/if}

		<!-- Canvas + inspector -->
		<div class="flex min-h-0 flex-1">
			<div class="min-w-0 flex-1">
				<FlowCanvas
					bind:nodes
					bind:selectedId
					{lookups}
					{issues}
					{actionTypes}
					canManage={data.canManage}
					onchange={markDirty}
					onrestructure={restructure}
					ondeleterequest={requestDelete}
				/>
			</div>
			{#if showIntake}
				<IntakeInspector
					{intake}
					canManage={data.canManage}
					onchange={markDirty}
					onclose={() => (selectedId = null)}
				/>
			{:else if selectedNode}
				<InspectorPanel
					node={selectedNode}
					{nodes}
					forms={data.forms}
					documents={data.documents}
					templates={data.templates}
					terms={data.terms ?? []}
					roles={data.roles}
					flows={data.flows}
					formFieldsEndpoint={endpoints.formFields}
					issues={issues.byNode[selectedNode.id] ?? []}
					canManage={data.canManage}
					onchange={markDirty}
					onrestructure={restructure}
					ondelete={() => requestDelete([selectedNode.id])}
					onclose={() => (selectedId = null)}
				/>
			{/if}
		</div>

		{#if issues.general.length}
			<div class="border-t px-5 py-2">
				<Alert variant="destructive">
					<AlertDescription>
						<ul class="list-inside list-disc space-y-1 text-sm">
							{#each issues.general as issue (issue)}
								<li>{issue}</li>
							{/each}
						</ul>
					</AlertDescription>
				</Alert>
			</div>
		{/if}
	</div>
{/if}

<Sheet.Root bind:open={dryRunOpen}>
	<Sheet.Content class="w-full">
		<Sheet.Header>
			<Sheet.Title>Dry run</Sheet.Title>
			<Sheet.Description>
				What this journey would do to a client like this, as it is drawn right now — including
				changes you have not saved. Nothing is performed.
			</Sheet.Description>
		</Sheet.Header>
		<div class="min-h-0 flex-1 overflow-y-auto p-4 pt-0">
			<FlowDryRun
				result={dryRunResult}
				error={dryRunError}
				pending={dryRunPending}
				clients={data.dryRunClients}
				actionName={data.canManage ? 'dryRun' : undefined}
				enhance={data.canManage ? dryRunSubmit : undefined}
			/>
		</div>
	</Sheet.Content>
</Sheet.Root>

<ConfirmDialog
	bind:open={deleteOpen}
	title={doomed.length > 1 ? `Delete ${doomed.length} nodes?` : 'Delete this node?'}
	description={deleteDescription}
	confirmLabel="Delete"
	destructive
	onconfirm={confirmDelete}
/>

<ConfirmDialog
	bind:open={leaveOpen}
	title="Discard unsaved changes?"
	description="You have unsaved changes. Leave without saving?"
	confirmLabel="Leave"
	destructive
	onconfirm={() => {
		leaveConfirmed = true;
		if (pendingHref) navigate(pendingHref);
	}}
/>
