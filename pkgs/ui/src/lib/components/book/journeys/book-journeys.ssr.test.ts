/**
 * SSR coverage for the shared journey surfaces (the portal waterfall: the platform pages became
 * thin routes over these, and the admin portal mounts them next over its own loaders).
 *
 * What SSR can pin — and what matters for the extraction contract:
 *  - links are INJECTED: every href in the markup comes from the `links` builders, never a
 *    hardcoded platform path (the admin routes differ, so a hardcoded href is a broken page);
 *  - actions are OPTIONAL: no `actions` ⇒ no step controls, no stop-journey, no task completion,
 *    and no start-journey entry point without `links.newJourneys` — read-only (the admin default);
 *  - with `actions`, the forms post to the APP'S OWN action names, not remembered ones.
 * Interaction (filters, sorting, pagination and dialogs) is client-side and out of SSR's reach.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import JourneysListing from './journeys-listing.svelte';
import JourneyDetail from './journey-detail.svelte';
import type { AppExecutionRow } from './applications.js';
import type { JourneyCase, JourneyExecution, JourneyStep } from './steps.js';
import type { TaskRow } from '../tasks/tasks.js';
import type {
	JourneyDetailActions,
	JourneyDetailLinks,
	JourneyStaff,
	JourneysListingLinks
} from './types.js';

// Deliberately NOT the platform's routes — the assertions below prove the components render
// whatever the app injects rather than remembering where they were extracted from.
const listingLinks: JourneysListingLinks = {
	journey: (uid) => `/x/journeys/${uid}`
};
const detailLinks: JourneyDetailLinks = {
	journeys: '/x/journeys',
	fillStep: (caseUid, stepKey, pid) =>
		`/x/journeys/ex1/steps/${caseUid}/${stepKey}${pid ? `?participant=${pid}` : ''}`,
	document: (uid) => `/x/documents/${uid}`
};

const row = (over: Partial<AppExecutionRow> = {}): AppExecutionRow => ({
	uid: 'j1',
	status: 'active',
	created_at: '2026-07-01T10:00:00Z',
	application_status: 'with_client',
	flow_name: 'Onboarding',
	adviser_name: 'Alex Morgan',
	clients: 2,
	accounts: 1,
	steps_total: 4,
	steps_done: 2,
	client_names: 'Harriet Vale, Tom Vale',
	...over
});

const step = (over: Partial<JourneyStep> = {}): JourneyStep => ({
	step_key: 'fact_find',
	step_type: 'fill_form',
	title: 'Fact find',
	assignee: 'staff',
	status: 'pending',
	participant_subject_id: 7,
	participant_name: 'Harriet Vale',
	request_item_id: 1,
	awaiting: null,
	last_event: null,
	document_uid: null,
	document_name: null,
	document_status: null,
	answers: null,
	...over
});

const profileCase = (over: Partial<JourneyCase> = {}): JourneyCase => ({
	uid: 'c1',
	kind: 'profile',
	subject_type: 'contact',
	subject_id: 7,
	subject_uid: 'con_7',
	subject_name: 'Harriet Vale',
	contact_type: 'individual',
	contact_email: null,
	roles: ['owner'],
	portal_status: 'none',
	portal_login_email: null,
	account_ref: null,
	platform_name: null,
	status: 'active',
	steps: [step()],
	...over
});

const execution = (over: Partial<JourneyExecution> = {}): JourneyExecution => ({
	uid: 'ex1',
	status: 'active',
	application_status: 'with_client',
	flow_name: 'Onboarding',
	custody: 'onshore',
	created_at: '2026-07-01T10:00:00Z',
	adviser_id: 'adv-1',
	adviser_name: 'Alex Morgan',
	assignee_user_id: null,
	assignee_name: null,
	cases: [profileCase()],
	timeline: [
		{ id: 1, event_type: 'execution.started', payload: {}, created_at: '2026-07-01T10:00:00Z' }
	],
	...over
});

const task = (over: Partial<TaskRow> = {}): TaskRow => ({
	id: 1,
	uid: 't1',
	subject_type: 'execution',
	subject_id: 'ex1',
	source_kind: 'flow',
	source_ref: null,
	assignee_kind: 'role',
	assignee_user_id: null,
	assignee_role_id: 2,
	assignee_contact_id: null,
	assignee_name: 'Compliance',
	category: 'review',
	title: 'Chase the missing ID',
	description: null,
	due_date: null,
	priority: 'normal',
	status: 'open',
	created_at: '2026-07-01T10:00:00Z',
	completed_at: null,
	...over
});

// Not the platform's names — proves the forms post to whatever the app wires up.
const actions: JourneyDetailActions = {
	enhance: () => {},
	action: '?/x-action',
	acknowledge: '?/x-acknowledge',
	retryStep: '?/x-retryStep',
	identityDocument: '?/x-identityDocument',
	identityManual: '?/x-identityManual',
	cancel: '?/x-cancel',
	completeTask: '?/x-completeTask'
};

describe('JourneysListing (SSR)', () => {
	const html = (
		rows: AppExecutionRow[],
		links: JourneysListingLinks = listingLinks,
		filters?: Record<string, string[]>
	) => render(JourneysListing, { props: { applications: rows, links, filters } }).body;

	it('renders the DataTable list with injected journey links and the requested columns', () => {
		const body = html([row()]);
		expect(body).toContain('href="/x/journeys/j1"');
		expect(body).toContain('Harriet Vale, Tom Vale');
		const order = [
			'>Clients<',
			'>Journey<',
			'>Adviser<',
			'>Assigned to<',
			'>Progress<',
			'>Stage<',
			'>Waiting on<',
			'>Status<'
		].map((header) => {
			// Inside the table: the open filter bar's chips name "Status" too.
			const at = body.indexOf(header, body.indexOf('<thead'));
			expect(at, header).toBeGreaterThan(-1);
			return at;
		});
		expect(order).toEqual([...order].sort((a, b) => a - b));
		expect(body).toContain('2 people · 1 account');
		expect(body).toContain('Onboarding');
		// When it began, under the journey's name.
		expect(body).toContain('1 Jul 2026, ');
		expect(body).toContain('Alex Morgan');
		expect(body).toContain('2/4');
		expect(body).toContain('Applicant Details');
		expect(body).toContain('Client');
		expect(body).toContain('In progress');
		expect(body).not.toContain('>Board<');
		expect(body).not.toContain('>List<');
	});

	// The progress cell printed the bar, the fraction AND the percentage, and "12/12 · 100%" was
	// wider than the box it was given — so it spilled into the Stage column and the two columns read
	// as one value. The percentage stays reachable (the progressbar announces it, the hover says it)
	// and only the printed duplicate goes.
	it('states progress as steps beside the bar, with the percentage announced not printed', () => {
		const body = html([row({ steps_done: 12, steps_total: 12 })]);
		expect(body).toContain('12/12');
		expect(body).not.toContain('12/12 · 100%');
		expect(body).toContain('aria-valuenow="100"');
		expect(body).toContain('12 of 12 steps · 100%');
	});

	it('says a journey with no steps yet is not started, rather than printing 0/0', () => {
		const body = html([row({ steps_done: 0, steps_total: 0 })]);
		expect(body).toContain('Not started');
		expect(body).not.toContain('0/0');
	});

	it('has useful list filters and paginates at 25 rows', () => {
		const body = html(
			Array.from({ length: 26 }, (_, index) =>
				row({ uid: `j${index + 1}`, client_names: `Client ${index + 1}` })
			)
		);
		expect(body).toContain('Search clients, adviser, assignee, journey or reference');
		// The six filters live in the BAR, shut on arrival: the journeys still underway are what
		// the list shows, and that is not a filter the reader applied. `filter-bar.svelte` owns
		// which filters it lists once the funnel is opened.
		expect(body).toContain('aria-label="Show filters"');
		expect(body).toContain('1–25 of 26');
		expect(body).toContain('Client 25');
		expect(body).not.toContain('Client 26');
	});

	it('counts the pipeline in the stat tiles', () => {
		const body = html([
			row(),
			row({ uid: 'j2', application_status: 'completed', client_names: 'Ada Byron' })
		]);
		expect(body).toContain('In flight');
		expect(body).toContain('With the client');
		expect(body).toContain('journeys finished');
	});

	// FB-20260921-05: the tiles were figures with no way through to the rows they counted. They
	// are the list's own filter bar now — buttons that set it — rather than links to a `?view=`.
	it('opens on the journeys underway, with each tile a press of the filter bar', () => {
		const rows = [
			row(),
			row({
				uid: 'j2',
				status: 'completed',
				application_status: 'completed',
				client_names: 'Ada Byron'
			}),
			row({ uid: 'j3', status: 'cancelled', client_names: 'Grace Hopper' })
		];
		const body = html(rows);
		expect(body).not.toContain('href="?view=');
		expect(body).not.toContain('aria-pressed="true"');
		expect(body).toContain('aria-label="Show filters"');
		expect(body).toContain('Harriet Vale, Tom Vale');
		// Stopped and completed journeys are history: a filter away, not the first screen.
		expect(body).not.toContain('Ada Byron');
		expect(body).not.toContain('Grace Hopper');
		// The counts are the whole population, whatever the bar is cut to.
		expect(body).toContain('journeys finished');
		const cut = html(rows, listingLinks, { status: ['completed'] });
		expect(cut).toContain('Ada Byron');
		expect(cut).not.toContain('Harriet Vale, Tom Vale');
		expect(html(rows, listingLinks, { status: ['cancelled'] })).toContain('Grace Hopper');
	});

	it('orders by most recently started unless a column is chosen', () => {
		const body = html([
			row({ uid: 'old', created_at: '2026-05-01T10:00:00Z', client_names: 'Older Start' }),
			row({ uid: 'new', created_at: '2026-09-01T10:00:00Z', client_names: 'Newer Start' })
		]);
		expect(body.indexOf('Newer Start')).toBeLessThan(body.indexOf('Older Start'));
		expect(body).not.toContain('aria-sort="');
	});

	it('names the reader as Me in Waiting on, in its own tone', () => {
		const body = html([
			row({ owner_is_me: true }),
			row({ uid: 'j2', client_names: 'Ada Byron', assignee_name: 'Priya Nair' })
		]);
		const badge = (tone: string, text: string) =>
			new RegExp(`text-${tone}-strong[^>]*>(?:\\s|<!--[^>]*-->)*${text}<`);
		expect(body).toMatch(badge('success', 'Me'));
		expect(body).toMatch(badge('brand', 'Priya Nair'));
	});

	it('read-only by default: no start flows means no start-journey entry point', () => {
		expect(html([row()])).not.toContain('New journey');
	});

	it('renders the named New journey dropdown when the app provides published flows', () => {
		const body = html([row()], {
			...listingLinks,
			newJourneys: [
				{ code: 'onboarding', name: 'Onboarding', href: '/x/journeys/new?flow=onboarding' }
			]
		});
		expect(body).toContain('New journey');
		expect(body).not.toContain('Listing actions');
	});

	it('offers a row kebab only for a completed onboarding', () => {
		const withPdf: JourneysListingLinks = {
			...listingLinks,
			applicationPdf: (uid) => `/x/journeys/${uid}/application.pdf`
		};
		const finished = html(
			[
				row({
					uid: 'done',
					status: 'completed',
					application_status: 'completed',
					flow_code: 'onboarding',
					client_names: 'Isla Morgan'
				}),
				row({ uid: 'open', status: 'active', flow_code: 'onboarding' })
			],
			withPdf,
			{ status: ['active', 'completed'] }
		);
		// The menu item itself is closed, so SSR sees the trigger and not the download link.
		expect(finished).toContain('aria-label="Actions for Isla Morgan"');
		expect(finished).not.toContain('Actions for Harriet Vale, Tom Vale');
	});

	it('a failed load renders the error banner, never a fake-empty list', () => {
		const body = render(JourneysListing, {
			props: { applications: [], loadFailed: true, links: listingLinks }
		}).body;
		expect(body).toContain('Journeys couldn’t be loaded.');
		expect(body).not.toContain('No journeys yet.');
	});
});

describe('JourneyDetail (SSR)', () => {
	const html = (
		props: {
			execution?: JourneyExecution;
			tasks?: TaskRow[];
			staff?: JourneyStaff[];
			actions?: JourneyDetailActions | null;
		} = {}
	) =>
		render(JourneyDetail, {
			props: { execution: execution(), links: detailLinks, ...props }
		}).body;

	it('renders the record off the injected data, with the injected back link', () => {
		const body = html({ actions });
		expect(body).toContain('Onboarding');
		expect(body).toContain('href="/x/journeys"');
		expect(body).toContain('Harriet Vale');
		expect(body).toContain('Applicants');
		// One call to action at the top of the timeline, not a button on every row.
		expect(body).not.toContain('Do this next');
		expect(body).not.toContain('Fill in');
		expect(body).toContain('Start');
		expect(body).toContain('For Harriet Vale');
		expect(body).toContain('Assigned to Alex Morgan');
		expect(body).toContain('Applicant Details');
		expect(body).toContain('0 of 1 done');
		expect(body).toContain('Journey started'); // activity
		expect(body).toContain('Alex Morgan');
		expect(body).toContain('>Adviser<');
		expect(body).toContain('>Assigned to<');
		expect(body).toContain('Unassigned');
	});

	it('lets staff reassign the journey, and shows who it is assigned to when they cannot', () => {
		const writable = html({
			actions: { ...actions, assign: '?/x-assign' },
			staff: [{ user_id: 'staff-1', name: 'Priya Shah', email: 'priya@firm.test' }],
			execution: execution({ assignee_user_id: 'staff-1', assignee_name: 'Priya Shah' })
		});
		expect(writable).toContain('action="?/x-assign"');
		expect(writable).toContain('Priya Shah');
		expect(writable).toContain('Staff tasks on this journey go to this person.');
		const reading = html({
			actions: null,
			execution: execution({ assignee_name: 'Priya Shah' })
		});
		expect(reading).not.toContain('action="?/x-assign"');
		expect(reading).toContain('Priya Shah');
	});

	it('collapses repeated activity and says an invite result in words', () => {
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({
								step_key: 'account',
								title: 'Account details',
								participant_subject_id: null,
								participant_name: null
							}),
							step({ step_key: 'declaration', title: 'Declare and send to the client' })
						]
					})
				],
				timeline: [
					{
						id: 3,
						event_type: 'step.completed',
						payload: { step_key: 'account' },
						created_at: '2026-09-25T18:21:00Z'
					},
					{
						id: 2,
						event_type: 'step.completed',
						payload: { step_key: 'account' },
						created_at: '2026-09-25T18:20:00Z'
					},
					{
						id: 1,
						event_type: 'step.completed',
						payload: {
							step_key: 'declaration',
							reason: 'invited=2 skipped=0 failed=0'
						},
						created_at: '2026-09-25T18:19:00Z'
					}
				]
			})
		});
		expect(body).toContain('Account details');
		expect(body).toContain('2 times');
		expect(body).toContain('2 clients invited');
		expect(body).not.toContain('“Account details”');
		expect(body).not.toContain('invited=');
		expect(body).not.toContain('skipped=0');
	});

	it('labels who each activity row was for and who did it', () => {
		const body = html({
			execution: execution({
				timeline: [
					{
						id: 2,
						event_type: 'step.completed',
						payload: { step_key: 'fact_find', participant: 7, actor_name: 'Mark Alford' },
						created_at: '2026-09-25T18:21:00Z'
					}
				]
			})
		});
		expect(body).toMatch(/<dt[^>]*>For<\/dt>\s*<dd[^>]*>Harriet Vale<\/dd>/);
		expect(body).toMatch(/<dt[^>]*>By<\/dt>\s*<dd[^>]*>Mark Alford<\/dd>/);
	});

	it('groups repeated forms and names the applicant on each actionable row', () => {
		const applicantStep = (id: number, name: string) =>
			step({
				step_key: 'core',
				title: 'Applicant details',
				participant_subject_id: id,
				participant_name: name
			});
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						uid: 'david-case',
						subject_id: 1,
						subject_name: 'David Pavey',
						steps: [applicantStep(1, 'David Pavey')]
					}),
					profileCase({
						uid: 'john-case',
						subject_id: 2,
						subject_name: 'John Pavey',
						steps: [applicantStep(2, 'John Pavey')]
					}),
					profileCase({
						uid: 'lisa-case',
						subject_id: 3,
						subject_name: 'Lisa Pavey',
						steps: [applicantStep(3, 'Lisa Pavey')]
					})
				]
			}),
			actions
		});
		expect(body).toContain('Applicant details');
		expect(body).toContain('Not started');
		expect(body).toContain('Personal and contact details');
		expect(body).toContain('For David Pavey, John Pavey, Lisa Pavey');
		expect(body).toContain('Assigned to Alex Morgan');
		expect(body).toContain('Start');
		expect(body).not.toContain('Fill in');
		expect(body).not.toContain('>Continue<');
		for (const name of ['David Pavey', 'John Pavey', 'Lisa Pavey']) {
			expect(body).toContain(name);
		}
	});

	it('marks a child applicant with the child glyph, and states date of birth and contact details', () => {
		// `contact_type` is never `child` in the database; a child is an individual under 18 by
		// date of birth, the rule kickoff applies. The card wore a set of initials and up to six
		// badges, and nothing on it said the JISA holder was a child.
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						uid: 'finn',
						subject_id: 9,
						subject_uid: 'con_9',
						subject_name: 'Finn Hobbis',
						dob: '2014-03-03',
						contact_email: null,
						contact_phone: null,
						roles: ['owner'],
						steps: []
					}),
					profileCase({
						dob: '1981-02-01',
						contact_email: 'harriet@example.com',
						contact_phone: '+44 7700 900123',
						roles: ['owner', 'registered_contact']
					})
				]
			}),
			actions
		});
		expect(body).toContain('>Child<');
		expect(body).toContain('lucide-baby');
		expect(body).toContain('Born 3 Mar 2014');
		expect(body).toContain('Born 1 Feb 1981');
		expect(body).toContain('harriet@example.com');
		expect(body).toContain('+44 7700 900123');
		expect(body).toContain('Owner, Registered contact');
		// Roles are a sentence now, not a badge each.
		expect(body).not.toMatch(/data-slot="badge"[^>]*>(?:<!---->)?Owner</);
		expect(body).not.toContain('>HV<');
		expect(body).not.toContain('>FH<');
	});

	// The portal pill is the control, as on the client record: no separate Invite / Resend button
	// column beside the facts (it squeezed them into wrapping). With the write action the pill is a
	// menu trigger and the invite form is there, hidden; read-only it is a plain badge.
	it('makes the portal pill the invite control, and drops the button column', () => {
		const writable = html({
			execution: execution({
				cases: [profileCase({ portal_status: 'invited', contact_email: 'harriet@example.com' })]
			}),
			actions: { ...actions, invitePortal: '?/x-invitePortal' }
		});
		expect(writable).toContain('Portal: invited');
		expect(writable).toContain('aria-label="Portal access"');
		expect(writable).toContain('action="?/x-invitePortal"');
		expect(writable).toContain('name="contact_uid" value="con_7"');
		expect(writable).not.toContain('>Resend invite<');
		expect(writable).not.toContain('>Invite to portal<');

		// "No portal access" is shown again: with no button to say it, the pill is the only cue.
		const fresh = html({
			execution: execution({ cases: [profileCase({ portal_status: 'none' })] }),
			actions: { ...actions, invitePortal: '?/x-invitePortal' }
		});
		expect(fresh).toContain('No portal access');
		expect(fresh).toContain('aria-label="Portal access"');

		// Nothing to do once active; read-only never gets a trigger or a form.
		const active = html({
			execution: execution({ cases: [profileCase({ portal_status: 'active' })] }),
			actions: { ...actions, invitePortal: '?/x-invitePortal' }
		});
		expect(active).toContain('Portal: active');
		expect(active).not.toContain('aria-label="Portal access"');
		expect(active).not.toContain('action="?/x-invitePortal"');

		const reading = html({
			execution: execution({ cases: [profileCase({ portal_status: 'invited' })] }),
			actions: null
		});
		expect(reading).toContain('Portal: invited');
		expect(reading).not.toContain('aria-label="Portal access"');
		expect(reading).not.toContain('invitePortal');
	});

	// The form page already saves an amendment; the journey row is what never linked back to a
	// completed one. Amend appears on a done staff form only while the execution says so, and it
	// posts nowhere — it opens the same form the Continue button does.
	it('offers Amend on a completed form until the journey is declared and sent', () => {
		const done = execution({
			forms_amendable: true,
			cases: [
				profileCase({
					steps: [
						step({
							step_key: 'core',
							title: 'Applicant details',
							status: 'fulfilled',
							participant_subject_id: 7
						})
					]
				})
			]
		});
		const open = html({ execution: done, actions });
		expect(open).toContain('Amend');
		expect(open).toContain('href="/x/journeys/ex1/steps/c1/core?participant=7"');
		// Locked (declared, or a flow that never reopens forms): the row stays a record.
		const locked = html({
			execution: execution({ ...done, forms_amendable: false }),
			actions
		});
		expect(locked).not.toContain('Amend');
		const reading = html({ execution: done, actions: null });
		expect(reading).not.toContain('Amend');
	});

	it('says Continue once a step is done, and names the journey assignee on firm work', () => {
		const body = html({
			execution: execution({
				assignee_name: 'Priya Shah',
				cases: [
					profileCase({
						steps: [
							step({ step_key: 'core', title: 'Applicant details', status: 'fulfilled' }),
							step({
								step_key: 'pension',
								title: 'Pension information',
								participant_name: 'John Hobbis'
							})
						]
					})
				]
			}),
			actions
		});
		expect(body).toContain('>Continue<');
		expect(body).toContain('Pension information');
		expect(body).toContain('For John Hobbis');
		expect(body).toContain('Assigned to Priya Shah');
		expect(body).toContain('Done');
		expect(body).not.toContain('>Start<');
	});

	// Every account card printed its serial, its wrapper and nobody — the only name on one was the
	// JISA's kickoff child snapshot, arriving anonymously through the meta line, so a family's four
	// accounts read as four numbered boxes.
	it('names who is on each account, under the role that puts them there', () => {
		const accountCase = (over: Partial<JourneyCase>): JourneyCase =>
			profileCase({
				kind: 'account',
				subject_type: 'account',
				subject_name: null,
				roles: [],
				...over
			});
		const body = html({
			execution: execution({
				cases: [
					accountCase({
						uid: 'jisa',
						subject_id: 1347,
						status: 'blocked',
						product_type: 'JISA',
						product_name: 'Junior Individual Savings Account',
						jisa_child: { name: 'David Pavey' },
						account_parties: [
							{ name: 'David Pavey', role: 'owner' },
							{ name: 'Lisa Pavey', role: 'registered_contact' }
						]
					}),
					accountCase({
						uid: 'gia',
						subject_id: 1348,
						product_type: 'GIA',
						product_name: 'General Investment Account',
						account_parties: [
							{ name: 'John Pavey', role: 'owner' },
							{ name: 'Lisa Pavey', role: 'owner' }
						]
					})
				]
			}),
			actions
		});
		expect(body).toContain('Registered contact');
		expect(body).not.toContain('registered_contact');
		expect(body).toContain('Owners');
		expect(body).toContain('John Pavey &amp; Lisa Pavey');
		// The account's derived engine state belongs in the journey timeline. Repeating a large
		// "Blocked" badge on every draft account made the account summary look like four errors.
		expect(body).not.toContain('>Blocked<');
		// The child is a real party, so the kickoff snapshot must not print their name a second
		// time in the meta line beside it.
		expect(body.match(/David Pavey/g)).toHaveLength(1);
	});

	it('requires an explicit adviser declaration before the invitation handoff', () => {
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({
								step_key: 'declaration',
								step_type: 'task',
								title: 'Declare and send',
								participant_subject_id: null,
								request_item_id: null
							})
						]
					})
				]
			}),
			actions
		});
		const actionForm = body.match(/<form[^>]+action="\?\/x-action"[\s\S]*?<\/form>/)?.[0] ?? '';
		expect(actionForm).toContain('name="adviser_confirmed"');
		expect(actionForm).toContain('value="false"');

		// A closed Dialog is deliberately absent from SSR, so pin its copy at the component source.
		const source = readFileSync(new URL('./journey-detail.svelte', import.meta.url), 'utf8');
		expect(source).toContain(
			'I confirm that the information entered is correct, complete and not misleading.'
		);
		expect(source).toContain('Each eligible client is invited to set up portal access.');
	});

	it('nests each step under the stage it belongs to, and opens the current one', () => {
		// The page drew a 7-stage strip AND a flat work list from unrelated sources, with nothing
		// saying which stage a step was part of. `stageOfStep` is that edge, so the stages become
		// the headings. A stage with no steps (Draft, and Completed when nothing maps to it) is
		// omitted here: it never holds work on this view.
		const body = html({
			execution: execution({
				application_status: 'terms',
				cases: [
					profileCase({
						steps: [
							step({ step_key: 'core', status: 'fulfilled' }),
							step({ step_key: 'terms_a', step_type: 'accept_terms', assignee: 'client' }),
							step({ step_key: 'idv', step_type: 'verify_identity', title: 'Identity' })
						]
					})
				]
			}),
			actions
		});
		// Terms is current, so it is the stage rendered open.
		expect(body).toContain('<details open=""><summary');
		expect(body).toContain('Identity');
		// Draft (and any other stage with no steps) is not drawn on this view.
		expect(body).not.toContain('No work in this stage');
		expect(body).not.toContain('>Draft<');
		// The four-group work list is gone — the stages carry the work now.
		expect(body).not.toContain('Needs you now');
		expect(body).not.toContain('Only work the adviser can act on now.');
		expect(body).not.toContain('In progress or coming later');
	});

	it('says what the CLIENT still owes, beside what the adviser owes', () => {
		// The record answered "3 steps need you" and was otherwise silent on the client's side — the
		// question an adviser is asked while the client is sitting with them. `(0 ready now)` is the
		// distinction worth drawing: theirs, but held behind the adviser's own earlier work.
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({ step_key: 'terms_a', step_type: 'accept_terms', assignee: 'client' }),
							step({
								step_key: 'terms_b',
								step_type: 'accept_terms',
								assignee: 'client',
								awaiting: ['terms_a']
							})
						]
					})
				]
			}),
			actions
		});
		expect(body).toContain('Harriet Vale has 2 steps to do (1 ready now)');
	});

	it('is READ-ONLY when no actions are injected: no step controls, no stop, no task Done', () => {
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step(), // staff fill_form → would offer "Fill in"
							step({ step_key: 'idv', step_type: 'verify_identity', title: 'Identity' })
						]
					})
				]
			}),
			tasks: [task()]
		});
		expect(body).not.toContain('Stop journey');
		expect(body).not.toContain('Fill in');
		expect(body).not.toContain('value="passed"');
		expect(body).not.toContain('Refer');
		expect(body).not.toContain('Done');
		expect(body).toContain('Chase the missing ID'); // the task itself still shows
	});

	it('offers the step actions against the INJECTED action names when the app can write', () => {
		// A `review` step: the one type that IS a person's decision, so Pass / Refer belong on it.
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({ step_key: 'offshore_review', step_type: 'review', title: 'Compliance review' })
						]
					})
				]
			}),
			actions
		});
		expect(body).toContain('action="?/x-action"');
		expect(body).toContain('Pass');
		expect(body).toContain('Refer');
		expect(body).toContain('Stop journey');
	});

	it('never offers Pass / Refer on an identity check or an AML screen, even when it can write', () => {
		// Both are mandatory and settled by the check itself — the provider's result or the
		// client's identity documents; AML is automated end to end. A staff Pass button was a way
		// to record a check that never ran, and the API now refuses one.
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({ step_key: 'idv', step_type: 'verify_identity', title: 'Identity' }),
							step({ step_key: 'aml', step_type: 'aml_check', title: 'AML screen' })
						]
					})
				]
			}),
			actions
		});
		expect(body).not.toContain('value="passed"');
		expect(body).not.toContain('Refer');
		expect(body).toContain('Provide documents');
		expect(body).toContain('action="?/x-identityManual"');
		expect(body).toContain('With Graphene');
		// Neither line is a hold, so the stage does not list it as something being waited on.
		expect(body).not.toContain('Waiting for');
	});

	it('shows an identity check under review as with Graphene, never as the client’s to do', () => {
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({
								step_key: 'idv',
								step_type: 'verify_identity',
								title: 'Identity',
								assignee: 'client',
								status: 'waiting',
								identity_review: true
							})
						]
					})
				]
			}),
			actions
		});
		expect(body).toContain('With Graphene for review');
		expect(body).toContain('Assigned to Graphene');
		expect(body).not.toContain('Assigned to Harriet Vale');
		expect(body).not.toContain('Completes when the identity check does');
		// Nothing is left for the client, so the side panel does not say they have steps to do.
		expect(body).not.toContain('steps to do');
		expect(body).not.toContain('step to do');
	});

	it('shows ComplyCube’s refusal only when the payload carries it (operations)', () => {
		const idv = step({
			step_key: 'idv',
			step_type: 'verify_identity',
			title: 'Identity',
			assignee: 'client',
			status: 'waiting'
		});
		const firm = html({
			execution: execution({ cases: [profileCase({ steps: [idv] })] })
		});
		expect(firm).not.toContain('ComplyCube refused the last upload');
		const operator = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							{
								...idv,
								identity_provider_error: {
									message: 'Image resolution is too low.',
									status_code: 422,
									at: '2026-10-02T10:00:00+00:00'
								}
							}
						]
					})
				]
			})
		});
		expect(operator).toContain('ComplyCube refused the last upload');
		expect(operator).toContain('Image resolution is too low.');
	});

	it('records a terms acceptance only against an uploaded signed copy', () => {
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({ step_key: 'terms', step_type: 'accept_terms', title: 'Terms of business' })
						]
					})
				]
			}),
			actions
		});
		// The row is a button that opens the dialog; the form lives there, because it carries a
		// FILE and a closed dialog is not in the server render. What must NOT be on the page is the
		// old row form that posted the acknowledgement with nothing but a note.
		expect(body).toContain('Record acceptance');
		expect(body).not.toContain('action="?/x-acknowledge"');
	});

	it('shows the signed copy a terms acceptance was recorded against', () => {
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({
								step_key: 'terms',
								step_type: 'accept_terms',
								title: 'Terms of business',
								status: 'fulfilled',
								document_uid: 'doc9',
								document_name: 'terms-signed.pdf'
							})
						]
					})
				]
			})
		});
		expect(body).toContain('href="/x/documents/doc9"');
		expect(body).toContain('Signed copy');
		expect(body).toContain('terms-signed.pdf');
	});

	it('shows manual identity evidence and lets staff upload each outstanding item', () => {
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({
								step_key: 'idv',
								step_type: 'verify_identity',
								title: 'Identity',
								identity_choice: 'manual',
								identity_documents: [
									{
										key: 'photo_id',
										title: 'Photo of a government-issued ID',
										guidance: 'A passport or photocard driving licence.',
										format_hint: 'JPG, PNG or PDF',
										request_uid: 'req1',
										item_uid: 'item1',
										status: 'pending'
									},
									{
										key: 'proof_of_address',
										title: 'Proof of your address',
										guidance: 'A utility bill from the last three months.',
										format_hint: 'JPG, PNG or PDF',
										request_uid: 'req1',
										item_uid: 'item2',
										status: 'pending'
									}
								]
							})
						]
					})
				]
			}),
			actions
		});
		expect(body).toContain('Identity evidence');
		expect(body).toContain('Photo of a government-issued ID');
		expect(body).toContain('Proof of your address');
		expect(body).toContain('sm:grid-cols-2');
		expect(body).toContain('action="?/x-identityDocument"');
		expect(body).toContain('name="item_uid" value="item1"');
		expect(body).toContain('name="item_uid" value="item2"');
		// Choosing the file submits it. There is no second Upload control.
		expect(body).not.toContain('>Upload<');
		// THE REGRESSION: Pass / Refer sat on this row from the moment the step opened, so the
		// check could be recorded against evidence nobody had sent. The work is the upload.
		// `value="passed"` rather than the word, which the page uses elsewhere.
		expect(body).not.toContain('value="passed"');
		expect(body).not.toContain('Refer');
		expect(body).toContain('Waiting for photo ID');
	});

	it('shows the received evidence and still offers no decision — the engine completes the check', () => {
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({
								step_key: 'idv',
								step_type: 'verify_identity',
								title: 'Identity',
								identity_choice: 'manual',
								identity_documents: [
									{
										key: 'photo_id',
										title: 'Photo of a government-issued ID',
										guidance: 'A passport or photocard driving licence.',
										format_hint: 'JPG, PNG or PDF',
										request_uid: 'req1',
										item_uid: 'item1',
										status: 'fulfilled',
										document_uid: 'doc1'
									}
								]
							})
						]
					})
				]
			}),
			actions
		});
		expect(body).not.toContain('value="passed"');
		expect(body).not.toContain('Refer');
		expect(body).toContain('View document');
		expect(body).toContain('Completes when the identity check does');
	});

	it('links a staff fill_form step to the INJECTED fill route, participant included', () => {
		const body = html({ actions });
		expect(body).toContain('href="/x/journeys/ex1/steps/c1/fact_find?participant=7"');
		// One Start at the top of the card. The row title stays a link so a specific form
		// can still be opened, without a second button beside it.
		expect(body).toContain('>Start<');
		expect(body).not.toContain('Fill in');
		expect(body).toMatch(
			/<a[^>]*href="\/x\/journeys\/ex1\/steps\/c1\/fact_find\?participant=7"[^>]*>\s*Fact find\s*<\/a>/
		);
	});

	it('completes a task through the INJECTED action name', () => {
		const body = html({ tasks: [task()], actions });
		expect(body).toContain('action="?/x-completeTask"');
		expect(body).toContain('Done');
	});

	it('links a generated document through the INJECTED builder', () => {
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({
								step_type: 'generate_document',
								status: 'fulfilled',
								document_uid: 'doc1',
								document_name: 'Suitability pack'
							})
						]
					})
				]
			})
		});
		expect(body).toContain('href="/x/documents/doc1"');
		expect(body).toContain('Suitability pack');
	});

	it('renders submitted answers as a labelled register, not a key dump', () => {
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({
								title: 'Applicant details',
								status: 'fulfilled',
								answers: {
									dob: '1990-01-15',
									first_name: 'Ada',
									address: { line1: '12 Cheyne Walk', postcode: 'SW3 5RA' }
								}
							})
						]
					})
				]
			})
		});
		expect(body).toContain('Date of birth');
		expect(body).toContain('15 Jan 1990');
		expect(body).toContain('First name');
		expect(body).toContain('12 Cheyne Walk, SW3 5RA');
		expect(body).toContain('3 answers');
		expect(body).not.toContain('>dob<');
		expect(body).not.toContain('[object Object]');
	});

	it('renders catalogue names instead of stored model and fee package ids', () => {
		const body = html({
			execution: execution({
				cases: [
					profileCase({
						steps: [
							step({
								status: 'fulfilled',
								answers: { model_id: '37', fee_package_ids: ['6', '12', '13'] },
								answer_labels: {
									model_id: 'Balanced growth',
									fee_package_ids: ['Platform charge', 'Advice charge', 'Custody charge']
								}
							})
						]
					})
				]
			})
		});
		expect(body).toContain('Investment model');
		expect(body).toContain('Balanced growth');
		expect(body).toContain('Fee packages');
		expect(body).toContain('Platform charge, Advice charge, Custody charge');
		expect(body).not.toContain('>37<');
		expect(body).not.toContain('>6, 12, 13<');
	});

	it('says why a step has no action instead of rendering a dead control', () => {
		const body = html({
			execution: execution({
				cases: [profileCase({ steps: [step({ assignee: 'client' })] })]
			}),
			actions
		});
		expect(body).toContain('With the client');
	});
});
