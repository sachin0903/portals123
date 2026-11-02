/**
 * The firm's email view is a THIN MOUNT of the shared listing — one component, mounted here
 * and in the admin portal, with each page supplying only what an app can (its own links, loaders and
 * form actions). A second listing rendered here is how the two surfaces drift apart.
 *
 * WHY A SOURCE SCAN: both things worth pinning are invisible to the type system. An href is a plain
 * string, so `/tenants/…/edit` interpolated here would type-check perfectly and 404 in a browser; and
 * `actions` is a structurally valid object whether or not its ternary consults the permission, so handing
 * the write callbacks to a read-only user is a type-correct bug.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const RAW = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
/** Comments stripped: they discuss the very strings being scanned for (the mount's own comment
 *  explains why it passes no `loadDefaults`, which would satisfy a `toContain('loadDefaults')`). */
const STRIPPED = RAW.replace(/<!--[\s\S]*?-->/g, '')
	.replace(/\/\*[\s\S]*?\*\//g, '')
	.replace(/^\s*\/\/.*$/gm, '');

/**
 * The EMAIL MOUNT alone — the `<EmailTemplatesListing …>` element and its snippets.
 *
 * Scoped, because this page serves both template kinds now: the documents half legitimately has a
 * `<table`, a `DropdownMenu` and archive/restore actions, so scanning the whole file for those
 * would fail on code that is supposed to be there. What has to stay true is narrower and unchanged
 * in substance — the EMAIL view owns no listing of its own.
 */
function emailMount(text: string): string {
	const start = text.indexOf('<EmailTemplatesListing');
	expect(start, 'the page mounts EmailTemplatesListing').toBeGreaterThan(-1);
	const end = text.indexOf('</EmailTemplatesListing>', start);
	expect(end, 'the mount is closed').toBeGreaterThan(start);
	return text.slice(start, end);
}

const MOUNT = emailMount(STRIPPED);
/** The script block, for the link builder and the imports the mount relies on. */
const SCRIPT = STRIPPED.slice(0, STRIPPED.indexOf('</script>'));
const PAGE = SCRIPT + MOUNT;
const LOADER = readFileSync(new URL('./+page.server.ts', import.meta.url), 'utf8');

describe('the page is a mount, not a copy', () => {
	it('renders the shared listing and holds no listing of its own', () => {
		// The duplication has to be GONE, not merely unused: a second table here is how the two
		// surfaces drift apart.
		expect(SCRIPT).toContain("from '@graphene/ui/email-editor'");
		for (const gone of [
			'<table',
			'DropdownMenu',
			'ConfirmDialog',
			'VersionHistoryDialog',
			'TemplateSettingsDialog'
		])
			expect(MOUNT, gone).not.toContain(gone);
		// Measured on the MOUNT rather than the file: a mount is a props list, a re-implementation is
		// not, and the file itself is now as long as the documents listing it also holds.
		expect(MOUNT.split('\n').length).toBeLessThan(50);
	});
});

describe('this portal passes its OWN links', () => {
	it('addresses a template at /templates/emails/{uid}, through the app’s token-safe proxy', () => {
		// The admin mount's shape must never appear here, and version history must go through
		// `[id]/versions/+server.ts` — which holds the access token — never a direct API URL.
		expect(PAGE).toMatch(/edit:\s*\(uid: string\) =>\s*`\/templates\/emails\/\$\{uid\}\/edit`/);
		expect(PAGE).toMatch(
			/versions:\s*\(uid: string\) =>\s*`\/templates\/emails\/\$\{uid\}\/versions`/
		);
		expect(PAGE).not.toContain('/tenants/');
		expect(PAGE).not.toContain('apiUrl');
		// The listing addresses a template, never the collection: a uid-less versions URL would hit
		// the page route rather than the proxy and answer with HTML.
		expect(PAGE).not.toContain('/templates/emails/versions');
		// The pre-move top-level route is gone; a stale absolute link would 404 rather than misroute.
		expect(PAGE).not.toContain('/email-templates');
	});
});

describe('the write gate is the app’s, and it is conditional', () => {
	it('hands `actions` to the listing only when the loader said canManage', () => {
		// Never unconditional — that is the shape that would give a read-only user the kebab. Its own
		// grant, not the documents one: a firm may let someone author its emails and not its documents.
		expect(MOUNT).toMatch(/actions=\{data\.canManageEmails\s*\?/);
		expect(MOUNT).toContain(': null}');
		expect(MOUNT).not.toContain('actions={{');
	});

	it('passes NO loadDefaults action — a firm curates what it was given, it does not re-seed', () => {
		// The one real capability asymmetry with internal operations. Absent here ⇒ the shared listing
		// renders no "Load defaults" button (pinned in the component's own SSR test).
		expect(MOUNT).not.toContain('loadDefaults');
		expect(LOADER).not.toContain('load-defaults');
	});

	it('resolves the firm’s own permission family server-side', () => {
		// `email_templates:*` — the admin surface uses `model:*`/`tenants:*` and resolves them its own way.
		// Neither resolution belongs in the shared component.
		expect(LOADER).toContain("'email_templates:read'");
		expect(LOADER).toContain("'email_templates:write'");
		expect(PAGE).not.toContain('email_templates:');
	});

	it('wires a single delete action, and nothing that archives or restores', () => {
		// A template is deleted outright: there is no archived state to reach, so an archive/restore
		// action here would post to endpoints the API no longer serves.
		// Prefixed, because this route owns both kinds' actions and both have a "create".
		expect(MOUNT).toContain("delete: '?/emailDelete'");
		expect(MOUNT).not.toMatch(/\barchive:/);
		expect(MOUNT).not.toMatch(/\bunarchive/);
		expect(MOUNT).not.toContain('archivedTemplates');
		// The EMAIL list endpoint takes no `status`; the documents one legitimately does, so this is
		// scoped to the email fetch rather than the whole loader.
		const emailFetch = LOADER.slice(LOADER.indexOf('loadEmails'), LOADER.indexOf('loadError'));
		expect(emailFetch).not.toContain('status=archived');
	});

	it('states the delete consequence in the firm’s own voice, and promises no way back', () => {
		// The shared confirm states the two facts every surface must state (what is destroyed, and that
		// the code frees up) and then this sentence, which is the one thing it cannot know: what a
		// firm's clients receive instead. So the mount neither repeats those facts nor — as the archive
		// copy did — offers a restore the API cannot perform.
		expect(MOUNT).toContain('deleteConsequence');
		expect(MOUNT).toMatch(/Graphene’s built-in default/);
		expect(PAGE).not.toMatch(/restore it/i);
		expect(PAGE).not.toMatch(/version history/);
		expect(PAGE).not.toMatch(/free to use again/);
		expect(PAGE).not.toContain('clones the preset');
	});

	it('loads usage for the delete confirm on demand, not on first paint', () => {
		// Without a usage map the shared listing defaults to `{}` and the warning block never
		// renders. The page used to fetch notification rules in the loader; that made Emails wait
		// on a map only the confirm needs. The confirm now fetches `/templates/emails/usage`.
		expect(MOUNT).toContain('loadTemplateUsage');
		expect(MOUNT).toContain('/templates/emails/usage');
		expect(MOUNT).not.toContain('templateUsage={data.emailTemplateUsage}');
		expect(LOADER).not.toContain('buildTemplateUsage');
		expect(LOADER).not.toContain('/firm/notification-rules');
		expect(LOADER).not.toContain('/firm/notification-catalogue');
	});
});
