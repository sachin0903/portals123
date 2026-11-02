/**
 * The shell breadcrumb.
 *
 * The bug these pin: the crumb was built from the pathname with ONE hardcoded static child
 * (`documents/create`) and an otherwise unconditional entity-name lookup, so every other static child
 * route read "Documents / Untitled" — `/documents/requests`, `/documents/requests/{uid}` and
 * `/datasets/new` all did. The tests below cover the static children that exist today AND a static
 * child that doesn't, because the next one added must not need a third special case.
 */

import { existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { SECTION_LABELS, sectionBreadcrumb, withDocumentsGate } from './breadcrumb';

const labels = (crumbs: { label: string }[] | null): string[] => (crumbs ?? []).map((c) => c.label);

describe('sectionBreadcrumb', () => {
	it('labels a section root without linking it to itself', () => {
		// The root IS the current page, so it gets no href — a crumb that navigates nowhere is a dead
		// control, and it is also how every non-document section read before the nav restructure
		// widened SECTION_LABELS to the whole sidebar.
		expect(sectionBreadcrumb('/documents', '/documents', {})).toEqual([{ label: 'Documents' }]);
		expect(sectionBreadcrumb('/clients', '/clients', {})).toEqual([{ label: 'Clients' }]);
	});

	it('names a document detail page from the loaded document', () => {
		const crumbs = sectionBreadcrumb('/documents/doc_1', '/documents/[uid]', {
			doc: { name: 'Suitability report' }
		});
		expect(crumbs).toEqual([
			{ label: 'Documents', href: '/documents' },
			{ label: 'Suitability report' }
		]);
	});

	it('links the entity crumb on an edit page and keeps Edit as the leaf', () => {
		const crumbs = sectionBreadcrumb('/templates/tpl_1/edit', '/templates/[uid]/edit', {
			template: { name: 'Annual review' }
		});
		expect(crumbs).toEqual([
			{ label: 'Templates', href: '/templates' },
			{ label: 'Annual review', href: '/templates/tpl_1' },
			{ label: 'Edit' }
		]);
	});

	it('reads Requests, not Untitled, on the requests list', () => {
		// `requests`/`request` are the keys that loader sets — none of the four the old lookup knew.
		const crumbs = sectionBreadcrumb('/documents/requests', '/documents/requests', {
			requests: [{ uid: 'req_1' }]
		});
		expect(crumbs).toEqual([{ label: 'Documents', href: '/documents' }, { label: 'Requests' }]);
	});

	it('names a request detail after its client, under a linked Requests crumb', () => {
		const crumbs = sectionBreadcrumb('/documents/requests/req_1', '/documents/requests/[uid]', {
			request: { uid: 'req_1', contact_name: 'Bo Client' },
			documents: []
		});
		expect(crumbs).toEqual([
			{ label: 'Documents', href: '/documents' },
			{ label: 'Requests', href: '/documents/requests' },
			{ label: 'Bo Client' }
		]);
	});

	it('keeps the Generate document label', () => {
		expect(labels(sectionBreadcrumb('/documents/create', '/documents/create', {}))).toEqual([
			'Documents',
			'Generate document'
		]);
	});

	it('labels /templates/datasets/new without an entity in page.data', () => {
		// Three crumbs, because datasets are authored INSIDE the templates surface now: the middle one
		// is title-cased from the route segment, so no label entry is needed for it.
		expect(
			labels(sectionBreadcrumb('/templates/datasets/new', '/templates/datasets/new', {}))
		).toEqual(['Templates', 'Datasets', 'New dataset']);
	});

	it('title-cases a static child it has never been told about', () => {
		// The general fix: a new static child route must read correctly with no change to the map.
		expect(
			labels(sectionBreadcrumb('/documents/bulk-send', '/documents/bulk-send', { documents: [] }))
		).toEqual(['Documents', 'Bulk send']);
		expect(
			labels(
				sectionBreadcrumb('/forms/responses/resp_1', '/forms/responses/[rid]', {
					form: { name: 'Fact find' }
				})
			)
		).toEqual(['Forms', 'Responses', 'Fact find']);
	});

	it('never renders a dynamic segment as its raw id', () => {
		// A 404 or a permission-gated loader leaves no entity — the crumb must be a noun, not "Untitled"
		// (which reads as a real record with a blank name) and not the uid.
		expect(labels(sectionBreadcrumb('/documents/doc_1', '/documents/[uid]', {}))).toEqual([
			'Documents',
			'Document'
		]);
		expect(
			labels(sectionBreadcrumb('/documents/requests/req_1', '/documents/requests/[uid]', {}))
		).toEqual(['Documents', 'Requests', 'Request']);
	});

	it('ignores an entity whose name is blank or not a string', () => {
		expect(
			labels(sectionBreadcrumb('/documents/doc_1', '/documents/[uid]', { doc: { name: '  ' } }))
		).toEqual(['Documents', 'Document']);
		expect(
			labels(sectionBreadcrumb('/documents/doc_1', '/documents/[uid]', { doc: { name: 42 } }))
		).toEqual(['Documents', 'Document']);
	});

	it('shows the section alone when no route matched', () => {
		expect(labels(sectionBreadcrumb('/documents/nope/deeper', null, {}))).toEqual(['Documents']);
	});

	it('tolerates a layout group in the route id', () => {
		expect(
			labels(sectionBreadcrumb('/documents/requests', '/(app)/documents/requests', {}))
		).toEqual(['Documents', 'Requests']);
	});

	it('returns null outside the sections so the caller can use the nav label', () => {
		// The dashboard is the only sidebar destination that is not a section root, and `/search`,
		// `/account` and `/files` are reached from the chrome rather than the nav.
		expect(sectionBreadcrumb('/', '/', {})).toBeNull();
		expect(sectionBreadcrumb('/account', '/account', {})).toBeNull();
	});
});

/**
 * The nav restructure put Clients/Accounts/Transactions into the daily-work groups and the configuration
 * surfaces into Firm settings, and every one of those is now a breadcrumb root — so the trail has to
 * resolve for the routes that previously showed no crumb at all.
 */
describe('sectionBreadcrumb — every sidebar section', () => {
	it('resolves a crumb for each section root in the sidebar', () => {
		const roots = [
			['/clients', 'Clients'],
			['/accounts', 'Accounts'],
			['/transactions', 'Transactions'],
			['/transfers', 'Transfers'],
			['/journeys', 'Journeys'],
			['/tasks', 'Tasks'],
			['/documents', 'Documents'],
			['/team', 'Team'],
			['/branding', 'Studio'],
			['/flows', 'Flows'],
			['/templates', 'Templates'],
			['/forms', 'Forms'],
			['/profiles', 'Profiles'],
			['/automations', 'Automations'],
			['/content', 'Content'],
			['/emails', 'Emails'],
			['/activity', 'Activity'],
			['/data-sources', 'Data sources']
		] as const;
		for (const [path, label] of roots) {
			expect(labels(sectionBreadcrumb(path, path, {}))).toEqual([label]);
		}
	});

	it('names a client record from the loaded contact, not from its honorific', () => {
		// `detail.contact` has no `name` for an individual and its `title` is "Mr" — the generic
		// NAME_FIELDS lookup would render "Clients / Mr", so the crumb is composed like the header.
		expect(
			sectionBreadcrumb('/clients/12', '/clients/[uid]', {
				detail: {
					contact: { type: 'individual', title: 'Mr', first_name: 'Max', surname: 'Oakden' }
				}
			})
		).toEqual([{ label: 'Clients', href: '/clients' }, { label: 'Mr Max Oakden' }]);

		expect(
			labels(
				sectionBreadcrumb('/clients/12', '/clients/[uid]', {
					detail: { contact: { type: 'organisation', name: 'Oakden Holdings Ltd' } }
				})
			)
		).toEqual(['Clients', 'Oakden Holdings Ltd']);
	});

	it('names an account, a task and a flow from their own loaded entity', () => {
		expect(
			labels(sectionBreadcrumb('/accounts/7', '/accounts/[uid]', { account: { name: 'SIPP' } }))
		).toEqual(['Accounts', 'SIPP']);
		expect(
			labels(sectionBreadcrumb('/tasks/t_1', '/tasks/[uid]', { task: { title: 'Chase the LOA' } }))
		).toEqual(['Tasks', 'Chase the LOA']);
		expect(
			labels(sectionBreadcrumb('/flows/3', '/flows/[uid]', { flow: { name: 'Onboarding' } }))
		).toEqual(['Flows', 'Onboarding']);
	});

	it('lets a page name its own crumb', () => {
		// The escape hatch for a record whose display name is composed rather than a field: return
		// `crumb` from the load. It wins over the entity lookup.
		expect(
			labels(
				sectionBreadcrumb('/journeys/j_1', '/journeys/[uid]', {
					crumb: 'Onboarding · Max Oakden',
					execution: { uid: 'j_1' }
				})
			)
		).toEqual(['Journeys', 'Onboarding · Max Oakden']);
		expect(
			labels(sectionBreadcrumb('/accounts/7', '/accounts/[uid]', { crumb: ' SIPP ', account: {} }))
		).toEqual(['Accounts', 'SIPP']);
	});

	it('falls back to a noun on a detail page whose entity did not load', () => {
		expect(labels(sectionBreadcrumb('/journeys/j_1', '/journeys/[uid]', {}))).toEqual([
			'Journeys',
			'Journey'
		]);
		expect(labels(sectionBreadcrumb('/clients/12', '/clients/[uid]', {}))).toEqual([
			'Clients',
			'Client'
		]);
	});

	it('labels the static children the restructured nav leads to', () => {
		expect(labels(sectionBreadcrumb('/team/roles', '/team/roles', {}))).toEqual(['Team', 'Roles']);
		expect(labels(sectionBreadcrumb('/transfers/new', '/transfers/new', {}))).toEqual([
			'Transfers',
			'New transfer'
		]);
		expect(labels(sectionBreadcrumb('/journeys/new', '/journeys/new', {}))).toEqual([
			'Journeys',
			'Start a journey'
		]);
		expect(labels(sectionBreadcrumb('/flows/dead-letters', '/flows/dead-letters', {}))).toEqual([
			'Flows',
			'Failed journey actions'
		]);
	});

	it('names a terms record from the crumb its loader returns', () => {
		// The terms editor has no in-page back button, so this trail is the only way back to the
		// listing. `item` is not one of ENTITY_KEYS, which is what `crumb` exists for.
		expect(
			sectionBreadcrumb('/terms/trm_1', '/terms/[uid]', { crumb: 'Client terms of business' })
		).toEqual([{ label: 'Terms', href: '/terms' }, { label: 'Client terms of business' }]);
	});

	it('keeps the section transition key intact for every section', () => {
		// The content transition and the per-page <title> both key off the first path segment, so a
		// section's crumb root and that key must be the same word.
		for (const path of Object.keys(SECTION_LABELS)) {
			expect(`/${path}`.split('/')[1]).toBe(path);
			expect(labels(sectionBreadcrumb(`/${path}`, `/${path}`, {}))).toEqual([SECTION_LABELS[path]]);
		}
	});
});

describe('a record nested inside another record', () => {
	// `/clients/8/documents/doc_1` is about TWO records at once, and both are on `page.data` — the
	// client from the `[id]` layout, the document from the page. The generic "what is this page
	// about?" answer put one name in both crumbs: the client crumb read the document's name, or
	// (since a contact is checked first) the document crumb read the client's.
	const data = {
		detail: { contact: { title: 'Mr', first_name: 'Max', surname: 'Oakden' } },
		clientName: 'Mr Max Oakden',
		doc: { name: 'Annual review pack' }
	};

	it('names each level with its own record', () => {
		expect(
			sectionBreadcrumb('/clients/8/documents/doc_1', '/clients/[uid]/documents/[doc]', data)
		).toEqual([
			{ label: 'Clients', href: '/clients' },
			{ label: 'Mr Max Oakden', href: '/clients/8' },
			{ label: 'Documents', href: '/clients/8/documents' },
			{ label: 'Annual review pack' }
		]);
	});

	it('names a journey on the client record by the flow, not the client', () => {
		expect(
			sectionBreadcrumb('/clients/8/journeys/ex_1', '/clients/[uid]/journeys/[exec]', {
				detail: { contact: { title: 'Mr', first_name: 'Max', surname: 'Oakden' } },
				journey: { flow_name: 'Fact find' }
			})
		).toEqual([
			{ label: 'Clients', href: '/clients' },
			{ label: 'Mr Max Oakden', href: '/clients/8' },
			{ label: 'Journeys', href: '/clients/8/journeys' },
			{ label: 'Fact find' }
		]);
	});

	it('still names the client alone on the client record itself', () => {
		expect(sectionBreadcrumb('/clients/8', '/clients/[uid]', data)).toEqual([
			{ label: 'Clients', href: '/clients' },
			{ label: 'Mr Max Oakden' }
		]);
	});

	it('falls back to the singular when the document has not loaded', () => {
		// A 404 or a permission-denied load leaves `doc` null; the crumb must not borrow the client's
		// name for it.
		expect(
			labels(
				sectionBreadcrumb('/clients/8/documents/doc_1', '/clients/[uid]/documents/[doc]', {
					...data,
					doc: null
				})
			)
		).toEqual(['Clients', 'Mr Max Oakden', 'Documents', 'Document']);
	});

	it('labels the client record\'s Chat route "Messages" under the client\'s name', () => {
		// The route segment is `chat` (singular) — STATIC_LABELS is the escape hatch that keeps the
		// breadcrumb reading "Messages" without renaming the route.
		expect(labels(sectionBreadcrumb('/clients/8/chat', '/clients/[uid]/chat', data))).toEqual([
			'Clients',
			'Mr Max Oakden',
			'Messages'
		]);
	});

	it('leaves the flat document route reading exactly as it did', () => {
		expect(
			sectionBreadcrumb('/documents/doc_1', '/documents/[uid]', {
				doc: { name: 'Suitability report' }
			})
		).toEqual([{ label: 'Documents', href: '/documents' }, { label: 'Suitability report' }]);
	});
});

/**
 * EVERY CRUMB LINKS SOMEWHERE THAT EXISTS.
 *
 * The href was the pathname the crumb sat at, which assumes every ancestor segment is a page of
 * its own. `/templates/emails/{uid}/edit` broke that twice over: the Emails view is a QUERY on the
 * templates listing (`?kind=emails`), and an email template has no record page at all — so the
 * "Emails" crumb and the one carrying the template's NAME both pointed at 404s.
 *
 * Reading the routes off disk is the point. A map of known-good paths would need updating by the
 * same person who forgot the href, which is nobody.
 */
describe('a crumb never links to a route that does not exist', () => {
	const ROUTES = join(import.meta.dirname, '..', 'routes');

	/** Does a URL path resolve to a SvelteKit page? Query and hash are not part of the route. */
	const routeExists = (href: string): boolean => {
		const path = href.split(/[?#]/)[0];
		const segments = path.split('/').filter(Boolean);
		// Walk the tree, letting a literal segment match a `[param]` directory.
		let dirs = [ROUTES];
		for (const segment of segments) {
			const next: string[] = [];
			for (const dir of dirs) {
				for (const entry of readdirSync(dir, { withFileTypes: true })) {
					if (!entry.isDirectory()) continue;
					const name = entry.name;
					// Layout groups are invisible in a URL, so they match nothing and are stepped
					// THROUGH — the same rule `sectionBreadcrumb` applies to a route id.
					if (name.startsWith('(') && name.endsWith(')')) {
						dirs.push(join(dir, name));
						continue;
					}
					if (name === segment || /^\[.+\]$/.test(name)) next.push(join(dir, name));
				}
			}
			if (next.length === 0) return false;
			dirs = next;
		}
		return dirs.some((dir) => existsSync(join(dir, '+page.svelte')));
	};

	it('finds what it is meant to be checking', () => {
		// The guard on the guard: if the walk can find nothing, every assertion below passes
		// vacuously and this file stops being a test.
		expect(routeExists('/templates')).toBe(true);
		expect(routeExists('/clients/con_1')).toBe(true);
		expect(routeExists('/templates/emails/tpl_1')).toBe(false);
	});

	it('the old Emails crumb matched the WRONG route rather than none', () => {
		// Worth stating, because it is why nobody saw this: `/templates/emails` is not a 404. It
		// matches `/templates/[uid]`, so pressing the crumb loaded the template RECORD page for a
		// template whose uid is the literal string "emails" — an error about a missing template
		// rather than anything that looks like a broken link. A route existing is not the same
		// question as a route being the one the crumb means, which is why the override map is
		// explicit rather than derived.
		expect(routeExists('/templates/emails')).toBe(true);
		const crumbs = sectionBreadcrumb(
			'/templates/emails/tpl_1/edit',
			'/templates/emails/[uid]/edit',
			{}
		);
		const emails = (crumbs ?? []).find((c) => c.label === 'Emails');
		expect(emails?.href).toBe('/templates?kind=emails');
	});

	it("names a content item's crumb from its own title but does not link it — same shape as an email template", () => {
		// `/content/{uid}` has no page of its own — `edit` is the only route under it — so the
		// crumb the reader sees for the item is label-only, and it is named FROM `item.title`
		// rather than reading "Content / Content / Edit" (the section root's own label, reused).
		expect(routeExists('/content/con_1')).toBe(false);
		const crumbs = sectionBreadcrumb('/content/con_1/edit', '/content/[uid]/edit', {
			item: { title: 'Getting started guide' }
		});
		expect(labels(crumbs)).toEqual(['Content', 'Getting started guide', 'Edit']);
		const item = (crumbs ?? []).find((c) => c.label === 'Getting started guide');
		expect(item?.href).toBeUndefined();
	});

	// One path per shape the builder produces: a section root, a static child, a record, and the
	// nested email editor that prompted this.
	const PATHS: [pathname: string, routeId: string][] = [
		['/templates', '/templates'],
		['/templates/datasets/new', '/templates/datasets/new'],
		['/clients/con_1', '/clients/[uid]'],
		['/clients/con_1/chat', '/clients/[uid]/chat'],
		['/documents/requests/req_1', '/documents/requests/[uid]'],
		['/templates/emails/tpl_1/edit', '/templates/emails/[uid]/edit'],
		['/templates/tpl_1/edit', '/templates/[uid]/edit'],
		['/content/con_1/edit', '/content/[uid]/edit']
	];

	it.each(PATHS)('%s', (pathname, routeId) => {
		const crumbs = sectionBreadcrumb(pathname, routeId, {}) ?? [];
		expect(crumbs.length).toBeGreaterThan(0);
		for (const crumb of crumbs) {
			if (!crumb.href) continue;
			expect(routeExists(crumb.href), `${pathname}: "${crumb.label}" → ${crumb.href}`).toBe(true);
		}
	});
});

describe('withDocumentsGate', () => {
	// A document record stays reachable on documents:read alone, but its Documents crumb always
	// points at /documents — which takes documents:show too. Without it, that parent 403s.
	it('strips the Documents root href without documents:show', () => {
		const crumbs = sectionBreadcrumb('/documents/doc_1', '/documents/[uid]', {
			doc: { name: 'Suitability report' }
		})!;
		expect(withDocumentsGate(crumbs, false)).toEqual([
			{ label: 'Documents' },
			{ label: 'Suitability report' }
		]);
	});

	it('strips the Requests href on a request detail without documents:show', () => {
		const crumbs = sectionBreadcrumb('/documents/requests/req_1', '/documents/requests/[uid]', {
			request: { uid: 'req_1', contact_name: 'Bo Client' },
			documents: []
		})!;
		expect(withDocumentsGate(crumbs, false)).toEqual([
			{ label: 'Documents' },
			{ label: 'Requests' },
			{ label: 'Bo Client' }
		]);
	});

	it('leaves it linked with documents:show', () => {
		const crumbs = sectionBreadcrumb('/documents/doc_1', '/documents/[uid]', {
			doc: { name: 'Suitability report' }
		})!;
		expect(withDocumentsGate(crumbs, true)).toEqual(crumbs);
	});

	it('leaves every other section alone', () => {
		const crumbs = sectionBreadcrumb('/clients/12', '/clients/[uid]', {
			detail: { contact: { type: 'organisation', name: 'Oakden Holdings Ltd' } }
		})!;
		expect(withDocumentsGate(crumbs, false)).toEqual(crumbs);
	});
});

describe('the back label', () => {
	// The LAST crumb is what the back trail records as the name of the page you are leaving
	// ($lib/back-stack, via the root layout's `hereLabel`), so it has a second caller now: a drift
	// here changes what every back control in the platform is CALLED, not just what the topbar reads.

	it('names a record page by the record', () => {
		const last = (crumbs: { label: string }[] | null) => labels(crumbs).at(-1);
		expect(
			last(sectionBreadcrumb('/tasks/t1', '/tasks/[uid]', { task: { title: 'Chase the LOA' } }))
		).toBe('Chase the LOA');
		const client = { detail: { contact: { title: 'Mr', first_name: 'Max', surname: 'Oakden' } } };
		expect(last(sectionBreadcrumb('/clients/8', '/clients/[uid]', client))).toBe('Mr Max Oakden');
		expect(last(sectionBreadcrumb('/tasks', '/tasks', {}))).toBe('Tasks');
	});

	it('returns null for the dashboard, forcing the caller through the nav label', () => {
		// The case in the bug report: a task opened from the dashboard must go BACK to the dashboard,
		// and "Dashboard" is a name only the nav holds. See nav.test.ts, which pins the other half.
		expect(sectionBreadcrumb('/', '/', {})).toBeNull();
	});
});
