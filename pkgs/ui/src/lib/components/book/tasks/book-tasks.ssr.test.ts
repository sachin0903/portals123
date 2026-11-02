import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import TasksListing from './tasks-listing.svelte';
import TaskDetail from './task-detail.svelte';
import type { Enhance } from '../clients/form-enhance.js';
import type { TaskRow } from './tasks.js';
import type { TaskDetailLinks, TasksListingLinks } from './types.js';

// Deliberately NOT the platform's routes — the assertions below prove the components render
// whatever the app injects rather than remembering where they were extracted from.
const listingLinks: TasksListingLinks = {
	task: (uid) => `/x/tasks/${uid}`,
	journey: (id) => `/x/journeys/${id}`
};
const detailLinks: TaskDetailLinks = {
	tasks: () => '/x/tasks',
	journey: (id) => `/x/journeys/${id}`
};

// A noop is enough for SSR: `use:` directives never run on the server.
const enhance: Enhance = () => {};

/** The visible TEXT of a render — tags, SSR block markers and layout whitespace taken out. A name
 *  that may or may not be a link renders inside an `{#if}` and inside an `<a>`, so "Client: Ada
 *  Ashworth" is no longer one contiguous string in the markup; an assertion about the sentence a
 *  reader reads should not have to know that. Href assertions still run against the raw body. */
const seen = (body: string): string => body.replaceAll(/<[^>]*>/g, '').replaceAll(/\s+/g, ' ');

/** `<thead>` only ("Category"/"Priority" are also filter labels), and its header cells by slot. */
const head = (body: string): string => body.match(/<thead[\s\S]*?<\/thead>/)?.[0] ?? '';
const heads = (body: string): string[] => head(body).match(/data-slot="table-head"/g) ?? [];

const task = (over: Partial<TaskRow> = {}): TaskRow => ({
	id: 7,
	uid: 'tsk_chase',
	subject_type: null,
	subject_id: null,
	source_kind: 'manual',
	source_ref: null,
	assignee_kind: 'user',
	assignee_user_id: 'usr_1',
	assignee_role_id: null,
	assignee_contact_id: null,
	assignee_name: 'Priya Shah',
	category: 'follow_up',
	title: 'Chase the transfer form',
	description: 'ACME pension transfer',
	due_date: null,
	priority: 'normal',
	status: 'open',
	created_at: '2026-07-01T10:00:00Z',
	completed_at: null,
	...over
});

const flowTask = (over: Partial<TaskRow> = {}): TaskRow =>
	task({
		id: 8,
		uid: 'tsk_flow',
		subject_type: 'execution',
		subject_id: 'exe_9',
		source_kind: 'flow',
		title: 'Approve the onboarding pack',
		description: null,
		...over
	});

/** A task handed to a CLIENT — the row whose name is a link, and the reason the API now carries
 *  the contact's UID: `assignee_contact_id` is a serial and a client record is addressed by uid. */
const clientTask = (over: Partial<TaskRow> = {}): TaskRow =>
	task({
		id: 9,
		uid: 'tsk_client',
		assignee_kind: 'contact',
		assignee_user_id: null,
		assignee_contact_id: 41,
		assignee_contact_uid: 'cnt_ash',
		assignee_name: 'Ada Ashworth',
		...over
	});

/** …and a task ABOUT a (different) client, which the record never named at all. */
const aboutClient = (over: Partial<TaskRow> = {}): TaskRow =>
	clientTask({
		subject_type: 'contact',
		subject_id: '42',
		subject_contact_uid: 'cnt_bev',
		subject_contact_name: 'Bev Okafor',
		...over
	});

/** The client-record builder an app injects; deliberately not a real portal path. */
const clientLink = (uid: string) => `/x/clients/${uid}`;

describe('TasksListing (SSR)', () => {
	const mineUids = new Set(['tsk_chase', 'tsk_flow']);
	const base = { tasks: [task(), flowTask()], mineUids, links: listingLinks };
	/** An app that scopes: the platform inbox passes the scope it read from its own URL. */
	const scoped = (assignee: 'me' | 'all', over: Record<string, unknown> = {}) => ({
		...base,
		filters: { category: [], due: [], q: '', priority: [], assignee },
		onfilters: () => {},
		...over
	});

	it('renders the inbox with INJECTED task + journey links', () => {
		const body = render(TasksListing, { props: base }).body;
		expect(body).toContain('href="/x/tasks/tsk_chase"');
		expect(body).toContain('Chase the transfer form');
		expect(seen(body)).toContain('Staff: Priya Shah');
		// The flow-raised task links to its journey through the injected builder.
		expect(body).toContain('href="/x/journeys/exe_9"');
		expect(body).toContain('raised by a journey');
	});

	it('renders the inbox as the shared TABLE: toolbar above it, a column per field, Priority left, action column only with actions, headers kept when empty', () => {
		const body = render(TasksListing, { props: base }).body;
		const content = body.indexOf('data-slot="card-content"');
		const toolbar = body.indexOf('Open (');
		expect(content).toBeGreaterThan(-1);
		expect(toolbar).toBeGreaterThan(content);
		expect(body.indexOf('aria-label="Tasks"')).toBeGreaterThan(toolbar);
		expect(seen(head(body)).trim()).toBe('Task Assignee Category Due Priority');
		expect(seen(body)).toContain('ACME pension transfer');
		const cells = head(body).split('<th'); // Priority's cell is a badge, so it aligns left.
		expect(cells.find((c) => c.includes('Priority'))).not.toContain('text-center');
		expect(heads(body)).toHaveLength(6); // kind-icon slot, Task, Assignee, Category, Due, Priority
		const managed = render(TasksListing, {
			props: { ...base, actions: { enhance, complete: '?/xComplete' } }
		}).body;
		expect(heads(managed)).toHaveLength(7); // the action column, gated elsewhere
		const empty = render(TasksListing, { props: { ...base, tasks: [] } }).body;
		expect(seen(head(empty)).trim()).toBe('Task Assignee Category Due Priority'); // headers persist
	});

	it('marks a late task in the Due column', () => {
		const body = render(TasksListing, {
			props: { ...base, tasks: [task({ due_date: '2020-01-01' })] }
		}).body;
		expect(seen(body)).toMatch(/days overdue/);
		expect(body).toContain('text-destructive font-medium');
		expect(body).toContain('1 overdue'); // the summary count above the list
	});

	it('links a CLIENT assignee’s name to the INJECTED client record, by uid', () => {
		const body = render(TasksListing, {
			props: {
				...base,
				tasks: [clientTask()],
				links: { ...listingLinks, client: clientLink }
			}
		}).body;
		// By uid — never the serial `assignee_contact_id`, which the route cannot resolve.
		expect(body).toContain('href="/x/clients/cnt_ash"');
		expect(body).not.toContain('/x/clients/41');
		expect(seen(body)).toContain('Client: Ada Ashworth');
	});

	it('leaves the name plain with no client builder, and with no uid on the row', () => {
		const noBuilder = render(TasksListing, { props: { ...base, tasks: [clientTask()] } }).body;
		expect(noBuilder).not.toContain('/x/clients/');
		expect(seen(noBuilder)).toContain('Client: Ada Ashworth');
		const noUid = render(TasksListing, {
			props: {
				...base,
				tasks: [clientTask({ assignee_contact_uid: null })],
				links: { ...listingLinks, client: clientLink }
			}
		}).body;
		expect(noUid).not.toContain('/x/clients/');
		expect(seen(noUid)).toContain('Client: Ada Ashworth');
	});

	it('never links a STAFF assignee through the client builder', () => {
		const body = render(TasksListing, {
			props: { ...base, links: { ...listingLinks, client: clientLink } }
		}).body;
		expect(body).not.toContain('/x/clients/');
	});

	it('is READ-ONLY without actions: no create affordance and no mutating form', () => {
		const body = render(TasksListing, { props: base }).body;
		expect(body).not.toContain('New task');
		expect(body).not.toContain('action="?/');
	});

	it('posts to the app’s OWN action names, and complete alone brings no manage affordances', () => {
		const body = render(TasksListing, {
			props: { ...base, actions: { enhance, complete: '?/xComplete' } }
		}).body;
		expect(body).toContain('action="?/xComplete"');
		expect(body).not.toContain('New task');
		expect(body).not.toContain('Cancel task'); // no `cancel` passed
	});

	it('shows the manage affordances when their action names are passed', () => {
		const body = render(TasksListing, {
			props: {
				...base,
				actions: { enhance, complete: '?/xComplete', create: '?/xCreate', cancel: '?/xCancel' }
			}
		}).body;
		expect(body).toContain('New task');
		expect(body).not.toContain('Listing actions');
		expect(body).toContain('action="?/xCancel"');
	});

	it('shows the reader their OWN tasks, with the firm behind a named tab', () => {
		// The inbox opens on the reader's own work. Anything else is a queue of other people's
		// tasks, which is the state this replaced — and the scope is the API's `assignee=me`, so it
		// narrows the book rather than the window on screen.
		const body = render(TasksListing, { props: scoped('me', { otherOpenCount: 9 }) }).body;
		expect(body).toContain('Assigned to me (2)');
		expect(body).toContain('Everyone (9)');
	});

	it('counts the tab you are NOT on from the other scope', () => {
		const body = render(TasksListing, { props: scoped('all', { otherOpenCount: 1 }) }).body;
		expect(body).toContain('Everyone (2)');
		expect(body).toContain('Assigned to me (1)');
	});

	it('offers no scope at all to an app that does not scope', () => {
		// The ops mount reads a firm's book from outside it, where "assigned to me" can only ever be
		// empty — so the control is absent and every task is shown, rather than a tab that lies.
		const body = render(TasksListing, {
			props: { ...base, filters: { category: [], due: [] }, onfilters: () => {} }
		}).body;
		expect(body).not.toContain('Assigned to me');
		expect(body).toContain('Chase the transfer form');
	});

	it('badges your own rows only while reading everyone else’s', () => {
		// On your own tab every row would carry it, which says nothing. It answers what the assignee
		// line cannot: a task raised against a role you hold names the ROLE, not you.
		expect(render(TasksListing, { props: scoped('all') }).body).toContain('Yours');
		expect(render(TasksListing, { props: scoped('me') }).body).not.toContain('Yours');
	});

	it('keeps the scope when you look at what is done', () => {
		// Done used to be the third arm of the same control, so choosing it threw the scope away and
		// answered with everyone's completed work. They are separate questions and separate controls.
		const body = render(TasksListing, { props: scoped('me') }).body;
		expect(body).toContain('Open (2)');
		expect(body).toContain('Done (0)');
	});

	it('says where the work is when your own inbox is empty', () => {
		// "No tasks" over a scoped book is the sentence that makes a reader stop trusting the
		// filter: it has to name what is sitting under the other tab.
		const body = render(TasksListing, {
			props: scoped('me', { tasks: [], otherOpenCount: 4 })
		}).body;
		expect(body).toContain('Nothing assigned to you');
		expect(body).toContain('4 open tasks are assigned to other people');
	});

	it('renders the filter controls only when the app wires `onfilters`', () => {
		// The filters live in the BAR now, reached through the funnel — so what an unfiltered arrival
		// offers is the funnel itself. A mount with no `onfilters` cannot apply anything (the app
		// owns the URL), so it gets no toolbar at all.
		expect(render(TasksListing, { props: base }).body).not.toContain('aria-label="Show filters"');
		expect(render(TasksListing, { props: { ...base, onfilters: () => {} } }).body).toContain(
			'aria-label="Show filters"'
		);
	});

	it('says a narrowed inbox is narrowed, without opening the panel over the rows', () => {
		const body = render(TasksListing, {
			props: {
				...base,
				filters: { category: [], due: ['overdue', 'today'], q: '', priority: [] },
				onfilters: () => {}
			}
		}).body;
		expect(body).toContain('aria-label="Hide filters (1 applied)"');
		expect(body).toContain('Overdue, Due today');
	});

	it('distinguishes the empty inbox and surfaces the app’s form error', () => {
		const empty = render(TasksListing, {
			props: { tasks: [], mineUids: new Set<string>(), links: listingLinks }
		}).body;
		// The full stop went when this became a title rather than a sentence in a panel. The
		// congratulation stays, because with no filters applied it is TRUE.
		expect(empty).toContain('No open tasks');
		expect(empty).not.toContain('match your filters');
		// A FILTER MISS IS NOT ALL CLEAR. This discriminated on the row count alone, so any of the
		// four filters congratulated the reader on an inbox they could not see.
		const filtered = render(TasksListing, {
			props: {
				tasks: [],
				mineUids: new Set<string>(),
				links: listingLinks,
				filters: { category: [], due: [], q: 'zzzz', priority: [] }
			}
		}).body;
		expect(filtered).toContain('No tasks match your filters');
		expect(filtered).not.toContain('all clear');

		const failed = render(TasksListing, {
			props: { ...base, formError: 'A title and assignee are needed.' }
		}).body;
		expect(failed).toContain('A title and assignee are needed.');
	});
});

describe('TaskDetail (SSR)', () => {
	const base = { task: flowTask(), links: detailLinks };

	it('renders the record off the injected data with INJECTED back + journey links', () => {
		const body = render(TaskDetail, { props: base }).body;
		expect(body).toContain('Approve the onboarding pack');
		expect(body).toContain('href="/x/tasks"'); // back to the inbox, injected
		expect(body).toContain('href="/x/journeys/exe_9"'); // source journey, injected
		expect(seen(body)).toContain('Staff: Priya Shah');
	});

	it('links BOTH the client assignee and the client the task is about, by uid', () => {
		const body = render(TaskDetail, {
			props: { task: aboutClient(), links: { ...detailLinks, client: clientLink } }
		}).body;
		expect(body).toContain('href="/x/clients/cnt_ash"'); // assigned to
		expect(body).toContain('href="/x/clients/cnt_bev"'); // about
		expect(seen(body)).toContain('Client: Ada Ashworth');
		expect(seen(body)).toContain('Bev Okafor');
		// The subject is its OWN field: a manual task about a client still reads as hand-made.
		expect(seen(body)).toContain('Created by hand');
	});

	// The reassign dialog's "Currently client: …" hint links the same name, but its content only
	// mounts when the dialog is OPEN, which SSR never is — that one is out of this file's reach.

	it('keeps both names plain text when the app passes no client builder', () => {
		const body = render(TaskDetail, { props: { task: aboutClient(), links: detailLinks } }).body;
		expect(body).not.toContain('/x/clients/');
		expect(seen(body)).toContain('Client: Ada Ashworth');
		expect(seen(body)).toContain('Bev Okafor');
	});

	it('is READ-ONLY without actions: status only, no transitions and no mutating form', () => {
		const body = render(TaskDetail, { props: base }).body;
		expect(body).toContain('Open'); // the status badge always renders
		expect(body).not.toContain('action="?/');
		expect(body).not.toContain('Mark done');
		expect(body).not.toContain('Edit');
	});

	it('complete alone brings Mark done but none of the manage affordances', () => {
		const body = render(TaskDetail, {
			props: { ...base, actions: { enhance, complete: '?/xComplete' } }
		}).body;
		expect(body).toContain('action="?/xComplete"');
		expect(body).toContain('Mark done');
		expect(body).not.toContain('Edit');
		expect(body).not.toContain('action="?/xStart"');
	});

	it('shows Start/Edit/Cancel when the app passes the manage action names', () => {
		const body = render(TaskDetail, {
			props: {
				...base,
				assignees: [{ value: 'user:usr_2', label: 'Marcus Wynn' }],
				actions: {
					enhance,
					complete: '?/xComplete',
					start: '?/xStart',
					cancel: '?/xCancel',
					edit: '?/xEdit'
				}
			}
		}).body;
		expect(body).toContain('Edit');
		expect(body).toContain('action="?/xStart"');
		expect(body).toContain('action="?/xCancel"');
	});

	it('narrates the activity trail and drops notification-stamp noise', () => {
		const body = render(TaskDetail, {
			props: {
				...base,
				activity: [
					{
						id: 1,
						created_at: '2026-07-01T10:00:00Z',
						action: 'tasks.tasks.insert',
						detail: null,
						actor_email: null,
						actor_name: null
					},
					{
						id: 2,
						created_at: '2026-07-02T09:00:00Z',
						action: 'tasks.tasks.update',
						detail: { changed: { assignee_notified_at: { old: null, new: 'x' } } },
						actor_email: 'ops@firm.test',
						actor_name: null
					},
					{
						id: 3,
						created_at: '2026-07-03T09:00:00Z',
						action: 'tasks.tasks.update',
						detail: { changed: { status: { old: 'open', new: 'done' } } },
						actor_email: null,
						actor_name: 'Priya Shah'
					}
				]
			}
		}).body;
		expect(body).toContain('Raised by a journey step'); // flow-sourced insert
		expect(body).toContain('Marked done by Priya Shah');
		expect(body).toContain('Activity');
		// The notification stamp is filtered out of the trail entirely.
		expect(body).not.toContain('ops@firm.test');
	});
});
