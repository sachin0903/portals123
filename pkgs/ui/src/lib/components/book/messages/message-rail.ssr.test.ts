/**
 * SSR coverage for the shared conversation rail — the client record's Messages section and the
 * platform inbox mount the same one, so a conversation carries the same context wherever it is
 * read from.
 *
 * Two things here are invisible from the thread and are what this pins. Its VISIBILITY is pure CSS
 * (`hidden lg:block` until the reader clicks the toggle), which is what makes it right before
 * hydration and impossible to flash. And its Files panel is derived from the LIVE message list
 * rather than from a fetch, so a file sent while the rail is open appears in it.
 */
import { describe, expect, it } from 'vitest';
import type { ComponentProps } from 'svelte';
import { render } from 'svelte/server';
import MessageRail from './message-rail.svelte';
import type { ApiRequest } from '../documents/index.js';
import type { TaskRow } from '../tasks/index.js';

const request = (over: Partial<ApiRequest> = {}): ApiRequest =>
	({
		uid: 'req_1',
		status: 'pending',
		items: [{ uid: 'itm_1', title: 'Passport', action: 'upload', status: 'pending' }],
		...over
	}) as ApiRequest;

const task = (over: Partial<TaskRow> = {}): TaskRow =>
	({
		id: 1,
		uid: 'tsk_1',
		title: 'Chase the transfer forms',
		status: 'open',
		due_date: null,
		priority: 'normal',
		...over
	}) as TaskRow;

const CLIENT = { name: 'Simple Accumulation', role: 'Client' };

type RailProps = ComponentProps<typeof MessageRail>;

/** The disclosure state bits-ui wrote on the band whose heading this is. */
const bandState = (body: string, title: string) => {
	const at = body.indexOf(`>${title}`) === -1 ? body.indexOf(title) : body.indexOf(`>${title}`);
	const marker = body.lastIndexOf('data-state="', at);
	return body.slice(marker + 12, body.indexOf('"', marker + 12));
};

/** Five wrappers, which is more than the band shows at once. */
const account = (n: number) => ({
	uid: `a${n}`,
	name: `Wrapper ${n}`,
	productType: 'ISA',
	value: 1000 * n,
	growth: 0
});

const html = (props: Partial<RailProps> = {}) =>
	render(MessageRail, { props: { people: [CLIENT], ...props } }).body;

describe('MessageRail', () => {
	it('does not collapse — it is part of the conversation, not a drawer on it', () => {
		// It used to fold away behind a toggle, which put the client's own details one click away on
		// every conversation. The only thing that still varies is the breakpoint: below `lg` there
		// is no room for two columns and it stacks under the thread.
		const asideClass = (body: string) =>
			body.slice(body.indexOf('<aside'), body.indexOf('>', body.indexOf('<aside')));
		const body = html();
		expect(body).toContain('aria-label="Client details"');
		expect(asideClass(body)).not.toContain('hidden');
		// A divider against the thread, not a second bordered box beside it.
		expect(body).toContain('lg:border-l');
	});

	it('leads with who the conversation is with, and a way to their record', () => {
		// The question the rail never answered while answering every other question about the
		// thread. The inbox can show any client in the book, so the name, the address you would
		// reply to and the road to the record all belong at the head of it.
		const body = html({
			client: { name: 'Rosemary Braun', email: 'rosemary@example.com', phone: '07700 900123' },
			clientHref: '/clients/abc123'
		});
		expect(body).toContain('Rosemary Braun');
		expect(body).toContain('mailto:rosemary@example.com');
		expect(body).toContain('07700 900123');
		// THE NAME IS THE LINK. A separate "View profile" beside it was two things to read for one
		// destination, and the name is what a reader reaches for anyway.
		expect(body).toContain('href="/clients/abc123"');
		expect(body).not.toContain('View profile');
	});

	it('states no status and no reference', () => {
		// "Active" is the one fact on this panel nobody opens a thread to read, and it took a row
		// of its own under the name; a provider reference is a filing code for talking to a
		// custodian. Neither is on `RailClient` any more, so neither can be computed for a row
		// that is never drawn — the record states both.
		const body = html({ client: { name: 'Rosemary Braun', serviceType: 'Advisory' } });
		expect(body).not.toContain('Onboarding');
		expect(body).not.toContain('Status');
		expect(body).not.toContain('Reference');
	});

	it('summarises what the client holds, by name, with its growth', () => {
		// "What is this conversation about" is usually a wrapper and a number, and the detail this
		// endpoint already reads carries both — so the band costs no request of its own.
		const body = html({
			accounts: [
				{ uid: 'a1', name: 'Oakden SIPP', productType: 'SIPP', value: 120_000, growth: 8000 },
				{ uid: 'a2', name: 'Oakden ISA', productType: 'ISA', value: 30_000, growth: 0 }
			]
		});
		expect(body).toContain('Accounts');
		// The account's own NAME, which is what an adviser calls it — and ONLY the name: the
		// provider's reference is a filing code for talking to a custodian, and it is on the
		// account's own page, which the row leads to.
		expect(body).toContain('Oakden SIPP');
		expect(body).not.toContain('AXF1');
		// GROWTH beside the balance: the second question anyone asks of a value.
		expect(body).toContain('£120,000');
		expect(body).toContain('+£8,000');
		// NO TOTAL. A rail states what they hold, not a valuation — the record is where a figure
		// that has to add up lives.
		expect(body).not.toContain('Total');
	});

	it('says nothing about accounts when there are none to summarise', () => {
		// Absent, not an empty band: a heading with nothing under it is a sentence about nothing —
		// the posture the two owed panels already take.
		expect(html()).not.toContain('Accounts');
	});

	it('counts an unvalued account as nothing, and says so on its row', () => {
		// A custodian that has not valued an account is not an account worth zero, but a total has
		// to be a number — so the row admits it while the sum carries on.
		const body = html({
			accounts: [{ uid: 'a1', name: 'New SIPP', productType: 'SIPP', value: null, growth: 0 }]
		});
		expect(body).toContain('New SIPP');
		expect(body).toContain('—');
	});

	it('states the facts an adviser would otherwise open the record for', () => {
		// The point of the panel: answer "who is this" beside the thread, so nobody leaves the
		// conversation — and the half-typed reply in the composer — to look one fact up. Reaching
		// them comes first, which is what a panel beside a live conversation is for.
		const body = html({
			client: {
				name: 'Rosemary Braun',
				email: 'rosemary@example.com',
				phone: '07700 900123',
				serviceType: 'Discretionary',
				dob: '1968-04-02',
				address: '12 Bell Street, London, NW1 5BY'
			}
		});
		expect(body).toContain('Email');
		expect(body).toContain('mailto:rosemary@example.com');
		// Dialled without the spaces a reader needs.
		expect(body).toContain('tel:07700900123');
		expect(body).toContain('DOB');
		expect(body).toContain('2 Apr 1968');
		expect(body).toContain('12 Bell Street, London, NW1 5BY');
		expect(body).toContain('Discretionary');
		// TYPE is gone: "Individual" beside a person's own name and face says nothing new. So are
		// REFERENCE and STATUS — a custodian's filing code, and the one fact nobody opens a thread
		// to read.
		expect(body).not.toContain('>Type<');
		expect(body).not.toContain('Reference');
	});

	it('leads with the client under NO heading, and does not fold them away', () => {
		// A "DETAILS" band over the client's own name was a label on the thing the panel is
		// obviously about, and a disclosure on the one part of the rail nobody wants hidden — it
		// answers "who am I talking to", which is what the rail is opened for.
		const body = html({ client: { name: 'Rosemary Braun', email: 'r@example.com' } });
		expect(body).not.toContain('Details');
		expect(body).toContain('Rosemary Braun');
		// And it is the FIRST thing: every band that folds comes after it.
		expect(body.indexOf('Rosemary Braun')).toBeLessThan(body.indexOf('People'));
	});

	it('states every fact, with an em dash where there is no answer', () => {
		// A field that vanishes when it is empty is a field nobody knows is missing, and "we have no
		// phone number for them" is itself an answer an adviser needs mid-conversation. The record
		// states them the same way, so the two cannot read as different sets of facts.
		const body = html({ client: { name: 'Rosemary Braun', serviceType: 'Advisory' } });
		expect(body).toContain('Advisory');
		expect(body).toContain('Service');
		expect(body).toContain('DOB');
		expect(body).toContain('Address');
		expect(body).toContain('Email');
		expect(body).toContain('—');
	});

	it('states the name without a road to it when the mount has no client route', () => {
		// The admin book's read-only mount has no `/clients` tree, so offering a button to one
		// would send an operator to a URL their portal does not have.
		const body = html({ client: { name: 'Rosemary Braun' } });
		expect(body).toContain('Rosemary Braun');
		expect(body).not.toContain('View profile');
	});

	it('says nothing about a client when there is not one to name', () => {
		// A GROUP is several clients, so one name would be a false statement about who is on the
		// other end; the client RECORD leaves it out because there the client is the page.
		const body = html();
		expect(body).not.toContain('View profile');
	});

	it('states the recent notes the firm has kept', () => {
		// "What do we already know about them" is a question asked mid-conversation, and the answer
		// was two clicks away on the record. Free from the same read as the rest of the panel.
		const body = html({
			notes: [
				{
					uid: 'n1',
					body: 'Prefers email to phone.',
					author: 'Craig Currie',
					createdAt: '2026-03-12T09:00:00Z'
				}
			]
		});
		expect(body).toContain('Notes');
		expect(body).toContain('Prefers email to phone.');
		expect(body).toContain('Craig Currie');
		expect(body).toContain('12 Mar 2026');
	});

	it('lets a note be withdrawn where it was written, when the mount can delete one', () => {
		// The record's Notes card has a delete; the rail listed the same notes with no way to take
		// one back, so a slip typed beside a thread meant a trip to the Overview to undo it.
		const note = {
			uid: 'n1',
			body: 'Prefers email to phone.',
			author: 'Craig Currie',
			createdAt: '2026-03-12T09:00:00Z'
		};
		const body = html({ notes: [note], onDeleteNote: async () => null });
		expect(body).toContain('aria-label="Delete this note"');
		// And none without the callback — a read-only mount, or a reader without `contacts:write`.
		expect(html({ notes: [note] })).not.toContain('Delete this note');
	});

	it('offers Notes even when there are none, and says so', () => {
		// ALWAYS OFFERED, unlike the other bands. "No notes" is a real and useful answer about a
		// client — it is the difference between nobody having written anything down and nobody
		// having looked. A missing band just reads as a rail that forgot to load.
		const body = html();
		expect(body).toContain('Notes');
		expect(body).toContain('No notes yet');
		expect(body).toContain('data-slot="empty-state"');
	});

	it('is collapsible bands, with People at the foot', () => {
		// The lists fold rather than tab: each answers a different question about the CLIENT, and a
		// tab strip made three of them compete for one slot. FILES is not among them — a file
		// belongs to the thread, so it is a tab on the conversation pane.
		const body = html();
		expect(body).toContain('People');
		expect(body).not.toContain('Team');
		expect(body).not.toContain('Files');
		expect(body).toContain('data-collapsible-trigger');
		expect(body).not.toContain('role="tablist"');
	});

	it('names everyone on the conversation, with their relationship', () => {
		// A band again rather than a popout, so the names are in the server's markup — and the role
		// is the relationship on THIS conversation, never a job title.
		const body = html({
			people: [
				CLIENT,
				{ name: 'Craig Currie', role: 'Adviser' },
				{ name: 'Mark Ross', role: 'Paraplanner' }
			]
		});
		expect(body).toContain('Craig Currie');
		expect(body).toContain('Adviser');
		expect(body).toContain('Mark Ross');
		expect(body).toContain('Paraplanner');
	});

	it('says so when the client is the only person named', () => {
		expect(html({ peopleNote: 'No adviser is assigned to this client.' })).toContain(
			'No adviser is assigned to this client.'
		);
		// With somebody else on it the note is silent — it answers "nobody else", not "here is a list".
		expect(
			html({
				people: [CLIENT, { name: 'Craig Currie', role: 'Adviser' }],
				peopleNote: 'No adviser is assigned to this client.'
			})
		).not.toContain('No adviser is assigned to this client.');
	});

	it('shows only what the client still owes', () => {
		// A fulfilled request is history and belongs on the Documents section; a cancelled one is not
		// owed at all. Showing either under "Pending client tasks" would be a false statement.
		const body = html({
			requests: [
				request(),
				request({ uid: 'req_2', status: 'fulfilled', items: [] }),
				request({ uid: 'req_3', status: 'cancelled', items: [] })
			],
			links: {
				request: (uid: string) => `/documents/requests/${uid}`,
				task: (uid: string) => `/tasks/${uid}`
			}
		});
		expect(body).toContain('/documents/requests/req_1');
		expect(body).not.toContain('/documents/requests/req_2');
		expect(body).not.toContain('/documents/requests/req_3');
		expect(body).toContain('Passport');
	});

	it('counts an IN-PROGRESS task as open, and flags an overdue one', () => {
		const body = html({
			tasks: [
				task({ uid: 'tsk_1', title: 'Started already', status: 'in_progress' }),
				task({ id: 2, uid: 'tsk_2', title: 'Late one', due_date: '2020-01-01' }),
				task({ id: 3, uid: 'tsk_3', title: 'Finished', status: 'done' })
			]
		});
		expect(body).toContain('Started already');
		expect(body).toContain('Late one');
		expect(body).not.toContain('Finished');
		expect(body).toContain('Overdue');
	});

	it('renders a request and a task unlinked when no `links` builder is injected', () => {
		// The admin portal mounts these surfaces read-only over its own routes; a rail that
		// hard-coded the platform's hrefs would send an operator to a page their portal has not got.
		const body = html({ requests: [request()], tasks: [task()] });
		expect(body).toContain('Passport');
		expect(body).toContain('Chase the transfer forms');
		expect(body).not.toContain('/documents/requests/req_1');
		expect(body).not.toContain('/tasks/tsk_1');
	});

	it('hides the two owed panels entirely when there is nothing owed', () => {
		const body = html();
		expect(body).not.toContain('Pending client tasks');
		expect(body).not.toContain('Open tasks');
	});

	it('bands them in the order they are asked for', () => {
		// What they hold and what we know about them answer "who is this and what is going on", which
		// is why the rail is read at all. The work outstanding and who else is on the thread are
		// checked deliberately, so they sit under it.
		const body = html({
			accounts: [account(1)],
			tasks: [task()]
		});
		expect(body.indexOf('Accounts')).toBeLessThan(body.indexOf('Notes'));
		// 'Tasks' capitalised is the band heading alone — the panel's own two lists are "Pending
		// client tasks" and "Open tasks".
		expect(body.indexOf('Notes')).toBeLessThan(body.indexOf('Tasks'));
		expect(body.indexOf('Tasks')).toBeLessThan(body.indexOf('People'));
	});

	it('opens the two that answer "who is this", and folds the two that are checked', () => {
		// Four open bands made the rail a page of its own to scroll. Tasks and People carry their
		// count on the closed band, so the fold still states whether there is anything to open.
		const body = html({ accounts: [account(1)], tasks: [task()] });
		expect(bandState(body, 'Accounts')).toBe('open');
		expect(bandState(body, 'Notes')).toBe('open');
		expect(bandState(body, 'Tasks')).toBe('closed');
		expect(bandState(body, 'People')).toBe('closed');
	});

	it('makes each account a road to the account, when the mount has one', () => {
		// "What is this conversation about" is usually a wrapper, and the next question is always
		// the wrapper itself. Unlinked without a builder, like the owed rows: the admin book has no
		// `/accounts` tree to send an operator to.
		const linked = html({
			accounts: [account(1)],
			links: {
				request: (uid: string) => `/documents/requests/${uid}`,
				task: (uid: string) => `/tasks/${uid}`,
				account: (uid: string) => `/accounts/${uid}`
			}
		});
		expect(linked).toContain('href="/accounts/a1"');
		expect(html({ accounts: [account(1)] })).not.toContain('/accounts/a1');
	});

	it('states three accounts and offers the rest', () => {
		// A client with eight wrappers pushed Notes and everything under it off the bottom of the
		// rail, and the first three answer "roughly what do they hold" on their own.
		const body = html({ accounts: [1, 2, 3, 4, 5].map(account) });
		expect(body).toContain('Wrapper 3');
		expect(body).not.toContain('Wrapper 4');
		expect(body).toContain('Show 2 more');
	});

	it('offers no "show more" when three is all there is', () => {
		const body = html({ accounts: [1, 2, 3].map(account) });
		expect(body).toContain('Wrapper 3');
		expect(body).not.toContain('more');
	});

	it('takes a note where it was heard, when the mount can write one', () => {
		// "They said on the phone they are retiring in March" is learned in a conversation, and a note
		// that needs a trip to the record and back is the note that never gets written.
		const body = html({ onAddNote: async () => null });
		expect(body).toContain('Add a note about this client');
		// THE RECORD'S OWN NOTES CARD, to the glyph: the `+` sits INSIDE the box's corner, not on a
		// button beneath it — one composer for one idea across the two places a note is written.
		expect(body).toContain('data-slot="input-group"');
		expect(body).toContain('aria-label="Add note"');
	});

	it('is the composer OR the empty state, never both', () => {
		// An empty state under a box whose placeholder already reads "Add a note about this client…"
		// says the same thing twice and pushes the box down the band.
		const body = html({ onAddNote: async () => null });
		expect(body).toContain('Add a note about this client');
		expect(body).not.toContain('No notes yet');
	});

	it('offers no composer to a reader who cannot write one', () => {
		// A read-only mount, or a reader without `contacts:write`: a box whose submit can only fail is
		// worse than no box.
		const body = html();
		expect(body).not.toContain('Add a note about this client');
		expect(body).not.toContain('Add note');
	});

	it('pins the client above the bands and scrolls only what is under them', () => {
		// Scrolling to a note or the fourth account must never scroll away the name, email and phone
		// of the person the whole panel is about — so the rail is a column with one scroller in it,
		// not one scroller.
		const body = html({ client: { name: 'Rosemary Braun', email: 'r@example.com' } });
		const asideTag = body.slice(body.indexOf('<aside'), body.indexOf('>', body.indexOf('<aside')));
		expect(asideTag).not.toContain('overflow-y-auto');
		expect(body.indexOf('Rosemary Braun')).toBeLessThan(body.indexOf('overflow-y-auto'));
	});
});

describe('MessageRail money figures', () => {
	it('splits the pence off the balance and growth', () => {
		const body = html({
			accounts: [
				{ uid: 'a1', name: 'Oakden SIPP', productType: 'SIPP', value: 120_000.5, growth: 8000.25 }
			]
		});
		expect(body).toMatch(/£120,000<span[^>]*>\.50<\/span>/);
		expect(body).toMatch(/\+£8,000<span[^>]*>\.25<\/span>/);
	});
});
