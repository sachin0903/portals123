/**
 * The operations email-templates page is a THIN MOUNT of the shared listing — the same component the
 * firm's own `/email-templates` mounts, so a fix to the delete confirm or the create rules reaches
 * both surfaces. A second listing rendered here is how the two drift apart.
 *
 * WHY A SOURCE SCAN, in the idiom of `tenant-links.test.ts` and `catalogue/switch-columns.test.ts`: an
 * href is a plain string (`/tenants/{t.id}` type-checks and 404s), and `actions` is a structurally valid
 * object whether or not its ternary consults the permission — so handing the write callbacks to a
 * read-only operator is a type-correct bug only a scan can catch.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const RAW = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
/** Comments stripped: they discuss the very strings being scanned for. */
const PAGE = RAW.replace(/<!--[\s\S]*?-->/g, '')
	.replace(/\/\*[\s\S]*?\*\//g, '')
	.replace(/^\s*\/\/.*$/gm, '');
const LOADER = readFileSync(new URL('./+page.server.ts', import.meta.url), 'utf8');

describe('the page is a mount, not a copy', () => {
	it('renders the shared listing and holds no listing of its own', () => {
		// The duplication has to be GONE, not merely unused — including the design law's named
		// anti-pattern, a `bg-muted` pill group standing in for `Segmented`.
		expect(PAGE).toContain("from '@graphene/ui/email-editor'");
		expect(PAGE).toContain('EmailTemplatesListing');
		for (const gone of [
			'<table',
			'DropdownMenu',
			'ConfirmDialog',
			'VersionHistoryDialog',
			'TemplateSettingsDialog',
			'bg-muted inline-flex'
		])
			expect(PAGE, gone).not.toContain(gone);
		// Counted on the raw file, comments included: a mount is short, a re-implementation is not.
		expect(RAW.split('\n').length).toBeLessThan(140);
	});
});

describe('this portal passes its OWN links', () => {
	it('addresses a template under the uid-addressed tenant section, via this app’s proxy', () => {
		// `tenant-links.test.ts` separately proves that interpolation is a uid and not a serial id. The
		// firm portal's shape must never appear here — it would 404 in this app — and version history must
		// point at the token-safe proxy, never `apiUrl`.
		expect(PAGE).toMatch(
			/const section = \$derived\(`\/tenants\/\$\{tenantUid\}\/email-templates`\)/
		);
		expect(PAGE).toMatch(/edit:\s*\(uid: string\) =>\s*`\$\{section\}\/\$\{uid\}\/edit`/);
		expect(PAGE).toMatch(/versions:\s*\(uid: string\) =>\s*`\$\{section\}\/\$\{uid\}\/versions`/);
		expect(PAGE).not.toMatch(/["'`]\/email-templates\//);
		expect(PAGE).not.toContain('apiUrl');
	});
});

describe('the write gate is the app’s, and it is the TARGET tenant’s family', () => {
	it('hands `actions` to the listing only when the loader said canManage', () => {
		expect(PAGE).toMatch(/actions=\{data\.canManage\s*\?/);
		expect(PAGE).toContain(': null}');
		expect(PAGE).not.toContain('actions={{');
	});

	it('resolves WHICH permission family server-side, through the shared scope helper', () => {
		// `model:*` for the preset, `tenants:*` for a firm (mirroring the API's `_require_tenant_scope`).
		// The page must not decide this, and the shared component must never learn a slug at all — the two
		// slugs appear here only as COPY in the read-only notice, telling the operator what to ask for.
		expect(LOADER).toContain("from '$lib/email-templates/scope'");
		expect(LOADER).toContain('templateScope');
		expect(PAGE).not.toContain('can(');
		expect(PAGE).toContain('model:write');
		expect(PAGE).toContain('tenants:write');
	});

	it('passes loadDefaults — the one capability the firm’s own surface does not have', () => {
		expect(PAGE).toContain("loadDefaults: '?/loadDefaults'");
		expect(LOADER).toContain('load-defaults');
	});

	it('hands the listing ONE destructive action, and the loader exports exactly it', () => {
		// The kebab's destructive item posts to whatever name the app gives it, so the two halves have to
		// agree; `archive`/`unarchive` no longer exist on either side of the contract.
		expect(PAGE).toContain("delete: '?/delete'");
		expect(LOADER).toMatch(/^\tdelete: async/m);
		for (const gone of ['archive', 'unarchive']) {
			expect(PAGE, gone).not.toContain(`${gone}: '?/${gone}'`);
			expect(LOADER, gone).not.toMatch(new RegExp(`^\\t${gone}: async`, 'm'));
		}
	});
});

describe('one list, because a template either exists or it does not', () => {
	it('asks the API for no `status` and carries no archived set', () => {
		// `live|archived|all` is gone from the API. A stale `?status=` would be ignored, so the tell is
		// here: no second fetch, and nothing for a view switch to segment.
		expect(LOADER).not.toContain('status=');
		expect(LOADER).not.toContain('archivedTemplates');
		expect(LOADER).not.toContain('archived_at');
	});
});

describe('the operations-only context survives the extraction', () => {
	it('keeps the acting-on-behalf banner, gated on writing a real firm (not the preset)', () => {
		// The tenant book's posture: edits here go to THIS firm's clients under their brand. It rides in on
		// the `notices` snippet — the seam that keeps it out of the shared component.
		expect(PAGE).toContain('Acting on behalf of');
		expect(PAGE).toMatch(/\{#if !data\.isModel && data\.canManage\}/);
		expect(PAGE).toContain('{#snippet notices()}');
	});

	it('branches the delete consequence for the MODEL preset, where the fact is different', () => {
		// Deleting a preset template changes no email being sent today; it changes what the NEXT firm
		// clones, and leaves the copies existing firms already hold. Collapsing the two sentences would
		// make one of them a lie.
		expect(PAGE).toMatch(/deleteConsequence=\{data\.isModel/);
		expect(PAGE).toContain('A new firm will clone the preset without this email');
		expect(PAGE).toContain('Firms that already hold a copy keep theirs');
		expect(PAGE).toMatch(/sent to \$\{firmName\}’s clients/);
	});

	it('states an IRREVERSIBLE consequence — nothing here offers to restore', () => {
		// The copy this replaced said "Until you restore it, …". There is no archive and no Restore, so
		// that sentence would promise a way back that does not exist.
		expect(PAGE).not.toMatch(/restore it/i);
		expect(PAGE).not.toContain('archivedTemplates');
		expect(PAGE).not.toMatch(/archiveConsequence/);
	});

	it('shows each row’s code — an operator curating the preset is curating the codes', () => {
		// A firm has no operation on the internal code, so its own surface hides it.
		expect(PAGE).toContain('showCodes');
	});
});
