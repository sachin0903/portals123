<script lang="ts">
	// A thin mount of the shared listing (`@graphene/ui/email-editor`), which the firm's own portal
	// mounts too. This surface serves ANY tenant, the model preset included — the model is just the
	// tenant whose published templates a new firm clones, so it gets different copy rather than a
	// different page.
	//
	// What stays here is what only this app knows: the loader, the operations permission family
	// (`model:write` for the preset, `tenants:write` for a firm — resolved server-side by
	// `$lib/email-templates/scope` into `data.canManage`), this app's uid-addressed URLs, the
	// acting-on-behalf posture, and SvelteKit's virtuals.
	import { deserialize, enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { Alert, AlertDescription } from '@graphene/ui';
	import { EmailTemplatesListing } from '@graphene/ui/email-editor';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	// The app's tenant routes are uid-addressed and tenant-links.test.ts scans for id-shaped
	// interpolations, so calling a uid `tenantId` both trips that guard and misleads the next reader.
	const tenantUid = $derived(page.params.uid);
	const section = $derived(`/tenants/${tenantUid}/email-templates`);
	const firmName = $derived(data.tenant?.name ?? 'this firm');
	const subject = $derived(data.isModel ? 'the model preset' : firmName);

	const links = $derived({
		edit: (uid: string) => `${section}/${uid}/edit`,
		// This app's own token-safe versions proxy, uid-addressed like every other tenant route.
		versions: (uid: string) => `${section}/${uid}/versions`,
		// So version history can SHOW a version, not only restore it.
		preview: (uid: string) => `${section}/${uid}/preview`
	});
	// BRANCHED LIKE THE TITLE: this page serves the MODEL PRESET and a real firm, and a firm has no
	// preset — "seed the preset" told an operator curating a firm's emails that they were editing
	// something else.
	const emptyDescription = $derived(
		data.isModel
			? 'Add the standard templates, then tailor the preset.'
			: 'Add the standard templates, then tailor them for this firm.'
	);
</script>

<svelte:head>
	<title
		>{data.isModel ? 'Model email templates' : `${firmName}: emails`} · Internal operations</title
	>
</svelte:head>

<EmailTemplatesListing
	eyebrow="Internal operations"
	title={data.isModel ? 'Model email templates' : 'Emails'}
	description={data.isModel
		? 'The standard email set for new firms. Add the default templates, then tailor them before use.'
		: `The emails ${firmName} sends. You can manage them on the firm's behalf, and the firm can change them later.`}
	back={data.isModel
		? { href: '/model', label: 'Model tenant' }
		: { href: `/tenants/${tenantUid}`, label: firmName }}
	templates={data.templates}
	catalog={data.catalog}
	loadError={data.loadError}
	{links}
	onretry={() => invalidateAll()}
	loadTemplateUsage={async () => {
		const res = await fetch(`/tenants/${tenantUid}/email-templates/usage`);
		if (!res.ok) return {};
		return (await res.json()) as Record<string, string[]>;
	}}
	deleteConsequence={data.isModel
		? 'A new firm will clone the preset without this email. Firms that already hold a copy keep theirs.'
		: `From now on Graphene’s standard version of this email will be sent to ${firmName}’s clients.`}
	emptyTitle={data.isModel ? 'No model email templates yet' : 'No email templates yet'}
	{emptyDescription}
	showCodes
	actions={data.canManage
		? {
				enhance,
				deserialize,
				create: '?/create',
				update: '?/update',
				delete: '?/delete',
				loadDefaults: '?/loadDefaults',
				onmutated: invalidateAll,
				navigate: (href) => goto(href)
			}
		: null}
>
	{#snippet notices()}
		{#if !data.isModel && data.canManage}
			<!-- The same posture the tenant book takes for write mode: editing here changes copy that goes
			     to THIS firm's clients under their brand, so say whose behalf you are acting on. Every
			     write is also stamped as operations-authored and lands in audit.actions naming the
			     operator. -->
			<Alert variant="warning" class="mb-4">
				<AlertDescription>
					Acting on behalf of {firmName}. Changes here go to their clients, and the firm can see
					that Graphene made them.
				</AlertDescription>
			</Alert>
		{/if}

		{#if !data.canManage}
			<!-- Naming the permission stays app-side: the shared listing never learns a slug, it only sees
			     whether it was handed an `actions` object. -->
			<Alert class="mb-4">
				<AlertDescription>
					You can view {subject}'s emails but not change them. Editing needs the
					<code>{data.isModel ? 'model:write' : 'tenants:write'}</code> permission.
				</AlertDescription>
			</Alert>
		{/if}

		{#if form && 'error' in form && form.error}
			<Alert variant="destructive" class="mb-4"
				><AlertDescription>{form.error}</AlertDescription></Alert
			>
		{/if}

		{#if form?.action === 'loadDefaults' && form.ok}
			<Alert variant="success" class="mb-4">
				<AlertDescription>
					{form.created === 0
						? `${data.isModel ? 'The model' : firmName} already has every standard template.`
						: `Added ${form.created} standard template${form.created === 1 ? '' : 's'}.`}
				</AlertDescription>
			</Alert>
		{/if}
	{/snippet}
</EmailTemplatesListing>
