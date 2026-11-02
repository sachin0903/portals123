/**
 * SSR pins for the shared client record: links are INJECTED, mutations only exist when `actions`
 * is passed (absent ⇒ read-only: no forms at all, details/bank as definition lists, no money
 * buttons, no cancel affordance), and the screening rail always states both rails.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import ClientRecord from './client-record.svelte';
import type { ClientRecordActions } from './client-record.svelte';
import { SOURCE_OF_WEALTH_OPTIONS } from './types';
import { normaliseAddress } from '../../ui/address-lookup/types.js';
import type { ClientRecordDetail, InstructionRow } from './types';
import type { ReviewRow } from '../reviews/types';

/**
 * The component's own source, with line endings normalised.
 *
 * Four cases below scan this file as TEXT, and one matches a needle spanning two lines. On Windows
 * with `core.autocrlf=true` the working copy becomes CRLF the moment git checks the file out — a
 * rebase is enough — so an LF needle stops matching and the case reads as the structure having
 * regressed. CI is Linux and never sees it, which is the worst shape of a fragile guard: green
 * where it runs, red only on the machine that just changed the file.
 */
const componentSource = (): string =>
	readFileSync(join(import.meta.dirname, 'client-record.svelte'), 'utf8').replace(/\r\n/g, '\n');

const detail = (over: Partial<ClientRecordDetail['contact']> = {}): ClientRecordDetail => ({
	contact: {
		id: 42,
		uid: 'con_abc123',
		type: 'individual',
		service_type: 'advised',
		enabled: true,
		name: 'Theo Caldwell',
		title: null,
		first_name: 'Theo',
		surname: 'Caldwell',
		email: 'theo@caldwell.test',
		mobile: '07700 900123',
		phone: null,
		address: null,
		...over
	},
	advisers: [
		{
			name: 'Zoe Lead',
			email: 'zoe@firm.test',
			job_title: 'Partner',
			is_adviser: true,
			role: 'adviser'
		}
	],
	accounts: [
		{
			id: 7,
			uid: 'acc_7',
			name: 'ISA',
			status: 'active',
			value: 100_000,
			product_name: 'Stocks & Shares ISA',
			product_type: 'ISA',
			is_pension: false,
			service_type: 'discretionary',
			model_id: 3,
			model_name: 'Balanced 60/40',
			cash: 8_000,
			growth: 12_500
		},
		{
			id: 8,
			uid: 'acc_8',
			name: 'SIPP',
			status: 'active',
			value: 50_000,
			product_name: null,
			product_type: 'SIPP',
			is_pension: true,
			service_type: null,
			model_id: null,
			model_name: null,
			cash: 2_000,
			growth: -500
		}
	],
	notes: [{ id: 1, body: 'Reviewed at annual meeting.', author: 'Zoe', created_at: '2026-07-01' }]
});

const links = {
	account: (uid: string) => `/tenants/t_1/book/accounts/${uid}`,
	client: (uid: string) => `/tenants/t_1/book/clients/${uid}`,
	illustrations: '/tenants/t_1/book/clients/42/illustrations',
	tasks: '/tasks',
	task: (uid: string) => `/tasks/${uid}`
};

const enhance = () => ({ destroy() {} });
const actions: ClientRecordActions = {
	enhance,
	updateClient: '?/updateClient',
	updateBankAccount: '?/updateBankAccount',
	setAccountModel: '?/setAccountModel',
	closeClient: '?/closeClient',
	restoreClient: '?/restoreClient',
	invitePortal: '?/invitePortal',
	createInstruction: '?/createInstruction',
	cancelInstruction: '?/cancelInstruction',
	addNote: '?/addNote'
};

it('shows the registered-contact relationship in both directions in Compliance', () => {
	const parent = render(ClientRecord, {
		props: {
			detail: {
				...detail({ first_name: 'Louise', surname: 'Byford', name: 'Louise Byford' }),
				relationships: [
					{
						id: 1,
						relationship_type: 'registered_contact',
						outbound: true,
						other_contact_id: 43,
						other_type: 'individual',
						other_name: 'Sam Junior',
						other_title: null,
						other_first_name: 'Sam',
						other_surname: 'Junior',
						other_uid: 'child_uid'
					}
				]
			},
			links,
			actions
		}
	}).body;
	expect(parent).toContain('Registered contact of');
	expect(parent).toContain('href="/tenants/t_1/book/clients/child_uid"');
	expect(parent).toContain('Sam Junior');

	const child = render(ClientRecord, {
		props: {
			detail: {
				...detail({ first_name: 'Sam', surname: 'Junior', name: 'Sam Junior' }),
				relationships: [
					{
						id: 1,
						relationship_type: 'registered_contact',
						outbound: false,
						other_contact_id: 42,
						other_type: 'individual',
						other_name: 'Louise Byford',
						other_title: 'Ms',
						other_first_name: 'Louise',
						other_surname: 'Byford',
						other_uid: 'parent_uid'
					}
				]
			},
			links,
			actions
		}
	}).body;
	expect(child).toContain('Registered contact');
	expect(child).not.toContain('Registered contact of');
	expect(child).toContain('href="/tenants/t_1/book/clients/parent_uid"');
	expect(child).toContain('Ms Louise Byford');
});

describe('term acceptances', () => {
	const accepted = (
		over: Partial<NonNullable<ClientRecordDetail['term_acceptances']>[number]>
	) => ({
		uid: 'acc_1',
		term_uid: 'term_1',
		title: 'Terms of business',
		code: 'tob',
		version_number: 3,
		accepted_at: '2026-07-14T10:00:00Z',
		channel: 'portal',
		...over
	});

	it('links the SIGNED COPY behind an in-person acceptance when the app can show documents', () => {
		// An adviser recording another person's agreement is one person's word for it; the scan
		// or photograph they uploaded is the evidence, and the record points at it.
		const body = render(ClientRecord, {
			props: {
				detail: {
					...detail(),
					term_acceptances: [
						accepted({ channel: 'in_person', evidence_document_uid: 'doc_signed' })
					]
				},
				links: { ...links, document: (uid: string) => `/tenants/t_1/book/documents/${uid}` }
			}
		}).body;
		expect(body).toContain('in person');
		expect(body).toContain('href="/tenants/t_1/book/documents/doc_signed"');
		expect(body).toContain('Signed copy');
	});

	it('still says the copy is on file where the mount has no document route', () => {
		const body = render(ClientRecord, {
			props: {
				detail: {
					...detail(),
					term_acceptances: [
						accepted({ channel: 'in_person', evidence_document_uid: 'doc_signed' })
					]
				},
				links
			}
		}).body;
		expect(body).toContain('signed copy on file');
		expect(body).not.toContain('doc_signed');
	});

	it('says nothing about a copy on an acceptance the client clicked themselves', () => {
		const body = render(ClientRecord, {
			props: {
				detail: { ...detail(), term_acceptances: [accepted({})] },
				links: { ...links, document: (uid: string) => `/d/${uid}` }
			}
		}).body;
		expect(body).toContain('Terms of business');
		expect(body).not.toContain('in person');
		expect(body).not.toContain('igned copy');
	});
});

const instruction = (over: Partial<InstructionRow> = {}): InstructionRow => ({
	uid: 'ins_1',
	kind: 'expected_in',
	amount: 5_000,
	status: 'pending',
	due_date: '2026-08-20',
	note: null,
	created_at: '2026-07-20T00:00:00Z',
	account_id: 7,
	account_uid: 'acc_7',
	account_name: 'ISA',
	model_name: null,
	...over
});

describe('ClientRecord (SSR)', () => {
	it('carries the client reference in the header, where the listing no longer does', () => {
		// It used to print under every name in the LISTING. Nobody scans a book of clients for an
		// id, but an adviser does read one out on a call and a custodian does ask for it — so it
		// sits with the rest of "how do I refer to this client", beside the email and the phone,
		// and is copyable for the same reason they are. Removing it from the row without this
		// would have lost it from the product.
		const body = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(body).toContain('abc123'); // `providerReference` strips the `con_` display prefix
		expect(body).toContain('Copy client reference');
	});

	it('opens the team and the profiles as POPOUTS, never a People page', () => {
		// They were one page carrying both sections, reached by a gear and a key that navigated away.
		// Assigning an adviser or linking a login is a one-field errand off the record, so each is a
		// dialog opened in place — and `links.people` is gone, so a stale href cannot come back.
		const body = render(ClientRecord, {
			props: { detail: { ...detail(), users: [] }, links, actions }
		}).body;
		expect(body).not.toContain('/people');
		expect(body).toContain('aria-label="Team actions"');
		expect(body).toContain('aria-label="Portal access"');
		expect(body).toContain('Team');
		expect(body).not.toContain('aria-label="Manage advisers"');
		// Buttons, not anchors: an `href` here is the old behaviour returning.
		for (const label of ['Team actions', 'Portal access']) {
			const at = body.indexOf(`aria-label="${label}"`);
			const tagOpensAt = body.lastIndexOf('<', at);
			expect(body.slice(tagOpensAt, at)).toContain('button');
		}
	});

	it('shows the portal-access badge only once a loader has supplied logins', () => {
		// Absent is not empty. The admin portal's read-only mount does not fetch `users`, and "No
		// portal access" against a read that never asked misreports the client rather than the load.
		//
		// The two states are told apart by the CONTROL, not by the words: the granted badge is a
		// button labelled "Client profiles", the withheld one a menu trigger labelled "Portal
		// access". This test used to lean on the badge reading "Portal Access" in Title Case while
		// everything around it was sentence case — so fixing that capitalisation broke the
		// assertion, which is the tell that it was never asserting on the right thing.
		const unasked = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(unasked).not.toContain('aria-label="Client profiles"');
		expect(unasked).not.toContain('aria-label="Portal access"');
		expect(unasked).not.toContain('No portal access');
		expect(unasked).not.toContain('Portal access');
		const none = render(ClientRecord, {
			props: { detail: { ...detail(), users: [] }, links }
		}).body;
		expect(none).toContain('aria-label="Portal access"');
		expect(none).toContain('No portal access');
		expect(none).not.toContain('aria-label="Client profiles"');
		const linked = render(ClientRecord, {
			props: {
				detail: {
					...detail(),
					users: [{ user_id: 'u1', capacity: 'self', name: 'Theo', email: 'theo@caldwell.test' }]
				},
				links
			}
		}).body;
		expect(linked).toContain('aria-label="Client profiles"');
		expect(linked).toContain('Portal access');
		expect(linked).not.toContain('No portal access');
	});

	it('states the details as a one-line-per-fact register, not a stacked grid', () => {
		// Label and value on ONE line, hairline-separated. The stacked `dt`-over-`dd` version spent
		// two lines and a 1rem gutter on every fact, and Address took a whole row of three columns to
		// say one line of text. Same fields, same order — a third of the height.
		const body = render(ClientRecord, {
			props: {
				detail: detail({
					address: { line1: '12 Cheyne Walk', city: 'London', postcode: 'SW3 5RA' },
					dob: '1946-01-23',
					nationality: ['GB']
				}),
				links
			}
		}).body;
		for (const value of [
			'Advised',
			'theo@caldwell.test',
			// The fixture stores `07700 900123` as the PRIMARY NUMBER; the register reads it back
			// through the same `formatPhoneNumber` the phone FIELD writes it in, so a record cannot
			// show one grouping where the form that captured it showed another.
			'Mobile',
			'+44 7700 900123',
			'12 Cheyne Walk, London, SW3 5RA',
			'23 Jan 1946',
			'GB'
		])
			expect(body).toContain(value);
		// Every row is one flex line: no `sm:col-span-2` band for the address any more.
		expect(body).not.toContain('sm:col-span-2 lg:col-span-3');
		expect(body).toContain('12 Cheyne Walk, London, SW3 5RA');
		// In a WIDE register a long value ellipses via TruncatedText (tooltip on actual overflow)
		// rather than wrapping or a native `title` — a wrapped value would ragged a three-column
		// card. `minmax(0, 1fr)` on the value track is what lets truncate fire: without it a
		// source-of-wealth label widens the row instead of clipping inside it. The narrow rail
		// takes the opposite rule; see 'wraps the value in the narrow rail'.
		expect(componentSource()).toContain('<TruncatedText text={row.value}');
		expect(componentSource()).toContain('minmax(0,1fr)');
	});

	it('drops the Phone row entirely when the client has none', () => {
		const body = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(body).not.toContain('>Phone<');
	});

	it('reads the other number as Phone', () => {
		const body = render(ClientRecord, {
			props: {
				detail: detail({ phone: '+441179000001' }),
				links
			}
		}).body;
		expect(body).toContain('Phone');
		expect(body).toContain('+44 117 900 0001');
	});

	it('keeps the mobile individuals-only and the phone open to everyone', () => {
		const source = componentSource();
		const mobile = source.indexOf('name="mobile"');
		const phone = source.indexOf('name="phone"');
		expect(mobile).toBeGreaterThan(-1);
		const gate = source.lastIndexOf('{#if !isOrg}', mobile);
		const closes = source.indexOf('{/if}', mobile);
		expect(gate).toBeGreaterThan(-1);
		expect(phone).toBeGreaterThan(closes);

		const org = render(ClientRecord, {
			props: {
				detail: detail({
					type: 'organisation',
					name: 'Caldwell Trust',
					mobile: null,
					phone: '+442079460958'
				}),
				links
			}
		}).body;
		expect(org).not.toContain('>Mobile<');
		expect(org).toContain('Phone');
		expect(org).toContain('+44 20 7946 0958');
	});

	it('states occupation and employment as SEPARATE facts (0116)', () => {
		// The whole reason they are two columns: one is whether they work, the other what they do,
		// and a retired GP is both. Collapsing them loses whichever half you drop — and Morgan
		// Lloyd models exactly this split (an employment-status option set, no occupation field).
		const body = render(ClientRecord, {
			props: {
				detail: detail({
					employment_status: 'retired',
					occupation: 'GP / Doctor',
					country_of_birth: 'United Kingdom',
					town_of_birth: 'Bath',
					source_of_funds: 'Personal savings',
					tax_relief: 'uk_relevant_earnings'
				}),
				links
			}
		}).body;
		expect(body).toContain('Employment');
		expect(body).toContain('Retired');
		expect(body).toContain('Occupation');
		expect(body).toContain('GP / Doctor');
	});

	it('shows the rest of the KYC answers that used to reach the record as dotted paths', () => {
		// Before 0116 every one of these folded into `crm.contacts.data` and appeared only under
		// the generic overflow flattener, labelled by its jsonb key.
		const body = render(ClientRecord, {
			props: {
				detail: detail({
					country_of_birth: 'United Kingdom',
					town_of_birth: 'Bath',
					source_of_funds: 'Personal savings',
					source_of_funds_detail: 'Sale of a second property',
					tax_relief: 'uk_relevant_earnings'
				}),
				links
			}
		}).body;
		// They are stated by the SECTION that owns each: birth is personal, source-of-wealth
		// facts are compliance, the tax-relief declaration is a pension one.
		expect(body).toContain('Country of birth');
		expect(body).toContain('Town of birth');
		// The declaration is rendered by its LABEL, never the stored slug.
		expect(body).toContain('Tax relief declared');
		expect(body).not.toContain('uk_relevant_earnings');
	});

	it('shows the resolved country NAME, not the stored ISO code', () => {
		// The columns store codes — `nationality` always did and `country_of_birth` does now, so
		// both are joinable to `ref.countries` and postable to a vendor. A record showing "GB" as
		// where somebody was born is showing the storage rather than the fact, so the record's
		// read resolves the names in the same query.
		const body = render(ClientRecord, {
			props: {
				detail: detail({
					country_of_birth: 'GB',
					country_of_birth_name: 'United Kingdom',
					nationality: ['GB', 'IE'],
					nationality_names: ['United Kingdom', 'Ireland']
				}),
				links,
				// The names come from the ONE vocabulary now (`ref.countries`, injected) rather than
				// from `*_name` columns resolved per read — same rule, one source, and it is what the
				// pickers offer too so the read and the edit cannot disagree.
				countries: [
					{ code: 'GB', name: 'United Kingdom' },
					{ code: 'IE', name: 'Ireland' }
				]
			}
		}).body;
		expect(body).toContain('United Kingdom');
		expect(body).toContain('Ireland');
		// The register does not render the bare code beside the name — one fact, one rendering.
		expect(body).not.toContain('>GB<');
	});

	it('falls back to the code when the reference join found nothing', () => {
		// A code `ref.countries` does not carry (a withdrawn one on an old record) must still
		// show SOMETHING: an empty row would report the client has no country of birth.
		const body = render(ClientRecord, {
			props: { detail: detail({ country_of_birth: 'AN', country_of_birth_name: null }), links }
		}).body;
		expect(body).toContain('Country of birth');
		expect(body).toContain('AN');
	});

	it('states every field of a section, answered or not', () => {
		// THE OPPOSITE OF THE OLD RULE, deliberately. The single register dropped any fact with no
		// value, which reads well for "whatever happens to be recorded" and terribly for a section a
		// firm is meant to COMPLETE — a field nobody can see is a field nobody fills in, which is
		// how `relevant_uk_earnings` went five migrations with no editor while the HMRC
		// relievable-limit check quietly ran on the £3,600 floor.
		const body = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(body).toContain('Country of birth');
		expect(body).toContain('Town of birth');
		// An em dash, not an absence — the gap is the point.
		expect(body).toContain('—');
	});

	it('links accounts through the injected builder — no hardcoded hrefs', () => {
		const body = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(body).toContain('href="/tenants/t_1/book/accounts/acc_7"');
		expect(body).toContain('href="/tenants/t_1/book/accounts/acc_8"');
		expect(body).not.toContain('href="/accounts/acc_7"');
		// The serial id is the fixture's `id: 7` — never an address.
		expect(body).not.toContain('/book/accounts/7"');
	});

	it('uses the same Value, Cash and Growth account columns as the book listing', () => {
		const body = render(ClientRecord, { props: { detail: detail(), links, actions } }).body;
		const headers = ['>Value<', '>Cash<', '>Growth<', '>Status<'].map((label) =>
			body.indexOf(label)
		);
		expect(headers.every((position) => position > -1)).toBe(true);
		expect(headers).toEqual([...headers].sort((a, b) => a - b));
		expect(body).toContain('£8,000');
		expect(body).toContain('+£12,500');
		expect(body).not.toContain('Set model');
		expect(body).not.toContain('Change model');
		// The wrapper is a tag; the full product name is gone — it was a second spelling of Type.
		expect(body).not.toContain('Stocks & Shares ISA');
		expect(body).toContain('>ISA<');
		expect(body).toContain('>SIPP<');
	});

	it('submits a note from the square note icon, and keeps authored line breaks', () => {
		// A filled send disc inside the composer read as a second primary action in a field that
		// already submits. The control is a ghost, no-fill square chat icon — the note, not a send
		// arrow. Line breaks the Textarea accepted must survive in the list — `whitespace-pre-wrap`
		// on the body, collapsed or expanded, because flattening them made a two-line note look
		// like one run-on sentence.
		const source = componentSource();
		const composer = readFileSync(join(import.meta.dirname, '../notes/composer.svelte'), 'utf8');
		const form = source.slice(
			source.indexOf('action={actions.addNote}'),
			source.indexOf('</form>', source.indexOf('action={actions.addNote}'))
		);
		expect(form).toContain('NoteComposer');
		expect(composer).toContain('MessageSquareIcon');
		expect(composer).toContain('variant="ghost"');
		expect(composer).toContain('text-foreground');
		expect(composer).not.toContain('rounded-full');
		expect(composer).not.toContain('SendHorizontalIcon');
		expect(composer).not.toContain('PlusIcon');
		expect(source).toContain("'text-xs whitespace-pre-wrap break-words'");
		expect(form).toContain('ctrlKey');
		expect(form).toContain('metaKey');
		expect(source).toContain('const NOTES_PAGE_SIZE = 10');
		expect(source).not.toContain('max-h-[32rem]');
		const heading = source.slice(
			source.indexOf('id="notes"'),
			source.indexOf('action={actions.addNote}')
		);
		expect(heading).toContain('>Notes</CardTitle>');
		expect(heading).not.toContain('detail.notes.length');
	});

	it('puts a compact four-item instruction component under Notes', () => {
		const instructions = Array.from({ length: 5 }, (_, index) =>
			instruction({
				uid: `ins_${index}`,
				account_name: `Account ${index}`,
				created_at: `2026-07-${String(20 + index).padStart(2, '0')}T00:00:00Z`
			})
		);
		const body = render(ClientRecord, {
			props: { detail: detail(), links, actions, instructions }
		}).body;
		expect(body.indexOf('>Notes<')).toBeLessThan(body.indexOf('>Recent instructions<'));
		expect(body).toContain('4 of 5');
		expect(body).toContain('Account 4');
		expect(body).toContain('Account 1');
		expect(body).toContain('href="/tenants/t_1/book/accounts/acc_7"');
	});

	it('read-only mode renders NO forms and no action buttons at all', () => {
		const body = render(ClientRecord, {
			props: { detail: detail(), links, instructions: [instruction()] }
		}).body;
		expect(body).not.toContain('<form');
		expect(body).not.toContain('Edit details');
		expect(body).not.toContain('Record money in');
		expect(body).not.toContain('Set model');
		expect(body).not.toContain('Archive');
		// The pending instruction is listed, but the cancel affordance is a mutation.
		expect(body).toContain('Expected in');
		expect(body).not.toContain('>Cancel<');
		// Details still read as a definition list.
		expect(body).toContain('theo@caldwell.test');
	});

	it('prints an account and its pending payment in that account’s currency', () => {
		const d = detail();
		d.accounts = [{ ...d.accounts[0], currency: 'USD', value: 1_000, cash: 20, growth: 5 }];
		const body = render(ClientRecord, {
			props: {
				detail: d,
				links,
				instructions: [instruction({ amount: 250, account_id: 7 })]
			}
		}).body;
		// Money splits the pence off the major figure, so the row is not one `$1,000.00` string.
		expect(body).toContain('$1,000');
		expect(body).toContain('$20');
		expect(body).toMatch(/\$250<span[^>]*>\.00<\/span>/);
		expect(body).not.toContain('US$');
		expect(body).not.toContain('£250');
	});

	it('actions injected ⇒ the mutations exist and post to the app’s own action names', () => {
		const body = render(ClientRecord, {
			props: { detail: detail(), links, actions, instructions: [instruction()] }
		}).body;
		// Every section opens in VIEW mode, so a pencil is the affordance on first render — the
		// forms appear only once one is used. Everything else still posts on sight.
		expect(body).toContain('Edit contact details');
		expect(body).toContain('Edit bank details');
		expect(body).not.toContain('Set model');
		expect(body).not.toContain('Change model');
		expect(body).toContain('action="?/addNote"');
		expect(body).toContain('action="?/closeClient"');
		expect(body).toContain('action="?/cancelInstruction"');
		// No money buttons on the record: an instruction targets an ACCOUNT, and the account record
		// offers the same four against the account you are looking at.
		expect(body).not.toContain('Record money in');
	});

	it('opens the editable panels in VIEW mode, behind their own pencil', () => {
		// A client record is read far more often than it is edited, and a permanent form says the
		// opposite — every value sitting in an input reads as unsaved work. So the panels render as
		// definition lists with a pencil, and the form appears only once it is used. The pencil is
		// per panel because details and bank details are separate saves against separate endpoints.
		const body = render(ClientRecord, { props: { detail: detail(), links, actions } }).body;
		expect(body).not.toContain('action="?/updateClient"');
		expect(body).not.toContain('action="?/updateBankAccount"');
		// Six pencils: contact, personal, pension, employer, bank, and Compliance (source of wealth
		// only — the ticks stay register-only).
		expect(body).toContain('Edit contact details');
		expect(body).toContain('Edit personal details');
		expect(body).toContain('Edit bank details');
		expect(body).toContain('Edit compliance');
		// SERVICE is gone with it: it is an ACCOUNT-level fact, so one value on the person was
		// either a summary that could be wrong or a default nothing enforced.
		expect(body).not.toContain('>Service<');
		expect(body).toContain('Address');
	});

	it('states bank details on the person — one set, used by their accounts', () => {
		const body = render(ClientRecord, {
			props: {
				detail: detail(),
				links,
				actions,
				bankAccount: {
					name: 'T Caldwell',
					bank: 'Barclays',
					sort_code: '20-00-00',
					account_number: '01234567'
				}
			}
		}).body;
		expect(body).toContain('Bank details');
		expect(body).toContain('T Caldwell');
		expect(body).toContain('Barclays');
		expect(body).toContain('logos/banks/GB/barc/round.svg');
		expect(body).toContain('20 - 00 - 00');
		expect(body).toContain('01234567');
		expect(body).toContain('Used by every account this client holds');
		expect(body).toContain('Edit bank details');
		expect(body).toContain('Account name');
		expect(body).not.toContain('action="?/updateBankAccount"');
	});

	it('shows a pending bank change as the case, who, why and the field diff', () => {
		const pending: ReviewRow = {
			uid: 'rev_bank_1',
			case_no: 12,
			action_key: 'contact.bank_details_changed',
			label: "Change a client's bank details",
			summary: null,
			before_label: 'No bank details',
			after_label: '20-00-00 · ****5678',
			event_type: 'client.bank_account_updated',
			subject_type: 'contact',
			subject_id: 'con_abc123',
			contact_uid: 'con_abc123',
			contact_name: 'Theo Caldwell',
			account_uid: null,
			account_name: null,
			justification: 'Client moved banks',
			reviewer_role_id: 1,
			reviewer_role_slug: 'compliance',
			reviewer_role_name: 'Compliance',
			proposed_by: 'user-1',
			proposed_by_name: 'Gio Matonti',
			proposed_at: '2026-09-22T10:00:00Z',
			status: 'pending',
			decided_by: null,
			decided_by_name: null,
			decided_at: null,
			decision_note: null,
			applied_at: null,
			is_mine: true,
			can_decide: false,
			payload: {
				name: 'Gio Matonti',
				sort_code: '20-00-00',
				account_number: '12345678'
			},
			before: { value: null }
		};
		const body = render(ClientRecord, {
			props: {
				detail: detail(),
				links: { ...links, review: (uid: string) => `/reviews?open=${uid}` },
				actions: { ...actions, cancelReview: '?/cancelReview' },
				pendingReviews: [pending]
			}
		}).body;
		expect(body).toContain('RV-12');
		expect(body).toContain('Waiting');
		expect(body).toContain('Gio Matonti');
		expect(body).toContain('Client moved banks');
		expect(body).toContain('12345678');
		expect(body).toContain('20 - 00 - 00');
		expect(body).not.toContain('••••');
		expect(body).toContain('href="/reviews?open=rev_bank_1"');
		expect(body).toContain('Cancel request');
		expect(body).not.toContain('waiting for review');
		// the card stays editable while the case waits: a second edit updates the case
		expect(body).toContain('Edit bank details');
		expect(body).toContain('Waiting for approval');
	});

	it('shows detail and vulnerability holds on the record, and withholds the vulnerability pencil', () => {
		const base = {
			event_type: 'client.details_updated',
			subject_type: 'contact',
			subject_id: 'con_abc123',
			contact_uid: 'con_abc123',
			contact_name: 'Theo Caldwell',
			account_uid: null,
			account_name: null,
			reviewer_role_id: 1,
			reviewer_role_slug: 'compliance',
			reviewer_role_name: 'Compliance',
			proposed_by: 'user-1',
			proposed_by_name: 'Gio Matonti',
			proposed_at: '2026-09-22T10:00:00Z',
			status: 'pending' as const,
			decided_by: null,
			decided_by_name: null,
			decided_at: null,
			decision_note: null,
			applied_at: null,
			is_mine: true,
			can_decide: false,
			before: null
		};
		const details: ReviewRow = {
			...base,
			uid: 'rev_det_3',
			case_no: 3,
			action_key: 'client.details_changed',
			label: 'Edit client details',
			summary: 'Edit client details',
			before_label: 'Current details',
			after_label: 'Change date of birth, first name',
			justification: 'Client corrected their name',
			payload: { contact_id: 43, fields: { first_name: 'Theo', dob: '1990-05-05' } }
		};
		const vulnerability: ReviewRow = {
			...base,
			uid: 'rev_vul_2',
			case_no: 2,
			action_key: 'client.vulnerability_updated',
			label: "Update a client's vulnerability record",
			summary: 'Change the vulnerability record',
			before_label: 'Not vulnerable',
			after_label: 'Vulnerable · 2 reasons',
			event_type: 'client.vulnerability_updated',
			justification: 'Told us on the call',
			payload: { contact_id: 43, vulnerability: { is_vulnerable: true, reasons: ['health'] } }
		};
		const body = render(ClientRecord, {
			props: {
				detail: detail(),
				links: { ...links, review: (uid: string) => `/reviews?open=${uid}` },
				actions: {
					...actions,
					cancelReview: '?/cancelReview',
					setVulnerability: '?/setVulnerability'
				},
				pendingReviews: [details, vulnerability]
			}
		}).body;
		expect(body).toContain('RV-3');
		expect(body).toContain('>First name<');
		expect(body).toContain('5 May 1990');
		expect(body).toContain('href="/reviews?open=rev_det_3"');
		expect(body).toContain('RV-2');
		expect(body).toContain('Drivers');
		expect(body).toContain('Health');
		expect(body).toContain('href="/reviews?open=rev_vul_2"');
		// the hold is said on the cards it will change, and in the hero for the vulnerability
		expect(body).toContain('Waiting for approval');
		expect(body).toContain('Vulnerability record');
		// every card stays editable while a hold waits: a second edit updates the case
		expect(body).toContain('Edit vulnerability');
		expect(body).toContain('Edit contact details');
	});

	it('shows the employer’s bank account on the Employer card and edits it with the bank inputs', () => {
		const d = detail({
			employer_name: 'Halloway Dental Ltd',
			employer_bank_account: {
				name: 'Halloway Dental Ltd',
				bank: 'MONZO BANK LIMITED',
				sort_code: '04-00-04',
				account_number: '01234567'
			}
		});
		const body = render(ClientRecord, { props: { detail: d, links } }).body;
		expect(body).toContain('MONZO BANK LIMITED');
		expect(body).toContain('04 - 00 - 04');
		expect(body).toContain('01234567');
		// nothing from the fact-find overflow, and no payment method: this is where money comes from
		expect(body).not.toContain('more from the fact-find');
		expect(body).not.toContain('Payment method');
		const editing = render(ClientRecord, { props: { detail: d, links, actions } }).body;
		expect(editing).toContain('Edit employer details');
	});

	it('no longer offers "Edit details" from the action bar', () => {
		// It was an anchor to #details, which is not something that happens to a client. A role whose
		// ONLY power is editing the client now gets no action bar at all rather than an empty one.
		const detailsOnly: ClientRecordActions = { enhance, updateClient: '?/updateClient' };
		const body = render(ClientRecord, {
			props: { detail: detail(), links, actions: detailsOnly }
		}).body;
		expect(body).not.toContain('href="#details"');
		// The pencils are still there — the affordance moved, it did not go.
		expect(body).toContain('Edit contact details');
	});

	it('contacts:write WITHOUT instructions:write — client editing stays, money goes', () => {
		// The six-splits posture (docs/api.md): the app omits the money-family action names when
		// the caller lacks instructions:write. Details/notes/status stay; every money-movement
		// affordance — the four buttons, the bank form, the mandate control, cancel — is absent.
		const clientOnly: ClientRecordActions = {
			enhance,
			updateClient: '?/updateClient',
			closeClient: '?/closeClient',
			addNote: '?/addNote'
		};
		const body = render(ClientRecord, {
			props: { detail: detail(), links, actions: clientOnly, instructions: [instruction()] }
		}).body;
		expect(body).toContain('Edit contact details');
		expect(body).toContain('action="?/addNote"');
		expect(body).toContain('action="?/closeClient"'); // the dialog; its trigger is in the kebab
		expect(body).not.toContain('Record money in');
		expect(body).not.toContain('Set model');
		expect(body).not.toContain('action="?/updateBankAccount"');
		expect(body).not.toContain('Edit bank details');
		expect(body).not.toContain('action="?/cancelInstruction"');
		// The instruction history still reads (instructions:read), only the pen is gone.
		expect(body).toContain('Expected in');
		expect(body).not.toContain('>Cancel<');
	});

	it('instructions:write WITHOUT contacts:write — money stays, client editing goes', () => {
		const moneyOnly: ClientRecordActions = {
			enhance,
			updateBankAccount: '?/updateBankAccount',
			setAccountModel: '?/setAccountModel',
			createInstruction: '?/createInstruction',
			cancelInstruction: '?/cancelInstruction'
		};
		const body = render(ClientRecord, {
			props: { detail: detail(), links, actions: moneyOnly, instructions: [instruction()] }
		}).body;
		// The money family stays wired for instruction cancellation, but model changes and the four
		// movement buttons are not controls on the client record.
		expect(body).not.toContain('Set model');
		expect(body).not.toContain('Change model');
		expect(body).toContain('action="?/cancelInstruction"');
		expect(body).toContain('Edit bank details');
		expect(body).not.toContain('Record money in');
		expect(body).not.toContain('Edit details');
		expect(body).not.toContain('action="?/updateClient"');
		expect(body).not.toContain('action="?/addNote"');
		expect(body).not.toContain('action="?/setEnabled"');
		expect(body).not.toContain('action="?/archiveClient"');
		// Client details fall back to the read-only definition list.
		expect(body).toContain('theo@caldwell.test');
	});

	it('hides the illustration entry point when the app injects neither handler nor link', () => {
		// Generating is `illustrations:write`, so an app without it passes no `onillustrate` AND no
		// `links.illustrations` — the shared `actions` fixture omits the handler, so nulling the link
		// is the whole of it. (This is the ADMIN book's posture: a read-only mount of a firm's client.)
		const body = render(ClientRecord, {
			props: { detail: detail(), links: { ...links, illustrations: null }, actions }
		}).body;
		expect(body).not.toContain('illustration');
	});

	it('offers the illustration in the header, named for the wrapper the client holds', () => {
		// THE REGRESSION THIS PINS: the button went with the action bar (ac14c925) and nothing took
		// its place, because unlike the money movements an illustration has no account to move to —
		// it is a projection for a PERSON, run with `account_id: null` for a prospect who holds
		// nothing. The drawer, the proxy route, the endpoint and the `onillustrate` contract all
		// stayed wired for weeks with no way in.
		const body = render(ClientRecord, {
			props: {
				detail: { ...detail(), users: [] },
				links,
				pensionLabel: 'SIPP',
				actions: { ...actions, onillustrate: () => {} }
			}
		}).body;
		expect(body).toContain('SIPP illustration');
		// A handler ⇒ a button that opens the form in place, never a navigation to the fallback
		// section — this app has no such section, and the href would be a 404.
		const at = body.indexOf('data-action="illustrate"');
		expect(at).toBeGreaterThan(-1);
		expect(body.slice(body.lastIndexOf('<', at), at)).toContain('button');
		expect(body).not.toContain(links.illustrations);
		// It is NOT in the overflow menu: that is for the rare, deliberate and recoverable, and
		// sitting beside Close is how this got lost the first time.
		expect(body.indexOf('SIPP illustration')).toBeLessThan(body.indexOf('Client actions'));
	});

	it('falls back to the injected link for a mount that hosts the form on its own page', () => {
		const body = render(ClientRecord, { props: { detail: detail(), links, actions } }).body;
		expect(body).toContain('Pension illustration'); // no pensionLabel ⇒ the generic word
		expect(body).toContain(`href="${links.illustrations}"`);
	});

	it('puts status in the header between type and portal access, and has no Deactivate', () => {
		const body = render(ClientRecord, {
			props: { detail: { ...detail(), users: [] }, links, actions }
		}).body;
		const typeAt = body.indexOf('>Individual<');
		const statusAt = body.indexOf('>Active<');
		const portalAt = body.indexOf('No portal access');
		expect(typeAt).toBeGreaterThan(-1);
		expect(statusAt).toBeGreaterThan(typeAt);
		expect(portalAt).toBeGreaterThan(statusAt);
		expect(body).not.toContain('Inactive');
		expect(body).not.toContain('Deactivate');
		expect(body).not.toContain('action="?/setEnabled"');
	});

	it('a suspended client shows Suspended in the header', () => {
		const body = render(ClientRecord, {
			props: { detail: detail({ provider_status: 'suspended' }), links, actions }
		}).body;
		expect(body).toContain('>Suspended<');
		expect(body).not.toContain('Deactivate');
	});

	it('offers Close only when the client holds nothing of value', () => {
		// The hidden form is always mounted when the action exists (the confirm dialog posts it);
		// the kebab item is the thing a valued book must not offer.
		const valued = render(ClientRecord, { props: { detail: detail(), links, actions } }).body;
		expect(valued).toContain('action="?/closeClient"');
		expect(valued).not.toContain('Client actions');

		const empty = render(ClientRecord, {
			props: { detail: { ...detail(), accounts: [] }, links, actions }
		}).body;
		expect(empty).toContain('Client actions');
		const source = componentSource();
		// `record?.`, not `actions?.`: the hero's action slot is a snippet named `actions`, so inside
		// it the snippet shadows this component's `actions` prop — `record` is that prop, aliased in
		// the script for exactly this reason.
		expect(source).toContain('record?.closeClient && closeable');
	});

	it('offers View their portal only once the client has a login', () => {
		const view = { ...actions, onviewas: () => {} };
		const none = render(ClientRecord, {
			props: { detail: { ...detail(), users: [] }, links, actions: view }
		}).body;
		expect(none).not.toContain('View their portal');
		expect(none).not.toContain('data-action="view-as"');

		const linked = render(ClientRecord, {
			props: {
				detail: {
					...detail(),
					users: [
						{
							user_id: 'u1',
							email: 'theo@caldwell.test',
							name: 'Theo',
							capacity: 'self'
						}
					]
				},
				links,
				actions: view
			}
		}).body;
		expect(linked).toContain('View their portal');
		expect(linked).toContain('data-action="view-as"');
	});

	it('No portal access is an action menu with Invite, not the profiles dialog', () => {
		const none = render(ClientRecord, {
			props: { detail: { ...detail(), users: [] }, links, actions }
		}).body;
		expect(none).toContain('aria-label="Portal access"');
		expect(none).not.toContain('aria-label="Client profiles"');
		expect(none).toContain('Client actions');
		const source = componentSource();
		expect(source).toContain('Invite to portal');
		expect(source).toContain('Manage profiles');

		const has = render(ClientRecord, {
			props: {
				detail: {
					...detail(),
					users: [
						{
							user_id: 'u1',
							email: 'theo@caldwell.test',
							name: 'Theo',
							capacity: 'self'
						}
					]
				},
				links,
				actions
			}
		}).body;
		expect(has).toContain('aria-label="Client profiles"');
		expect(has).toContain('Portal access');
		expect(has).not.toContain('aria-label="Portal access"');
		expect(has).not.toContain('Client actions');
	});

	it('states the account count, and says "of" once a filter narrows it', () => {
		// Unfiltered it is a plain total; the "n of m" form only appears when the two differ, so the
		// header never says "8 of 8".
		const body = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(body).toMatch(/\d+ accounts?/);
		expect(body).not.toMatch(/(\d+) of \1/);
	});

	it('offers New journey in the header Actions menu, one entry per flow the app passes', () => {
		// The menu's items render only once it opens, so the SSR body proves the control and the
		// source pins the entries; the account record wears the same outline "Actions" button.
		const body = render(ClientRecord, {
			props: {
				detail: detail(),
				links: {
					...links,
					newJourneys: [
						{ code: 'fact_find', name: 'Fact find', href: '/journeys/new?flow=fact_find&client=c1' }
					]
				}
			}
		}).body;
		expect(body).toContain('aria-label="Client actions"');
		const at = body.indexOf('aria-label="Client actions"');
		const trigger = body.slice(at, body.indexOf('</button>', at));
		expect(trigger).toMatch(/Actions\s*(<!---->)?$/);
		const source = componentSource();
		expect(source).toContain('<DropdownMenu.GroupHeading>New journey</DropdownMenu.GroupHeading>');
		expect(source).toContain('data-action="new-journey-{flow.code}"');
		expect(source).toContain('<a {...props} href={flow.href}>');

		// Without flows and without actions there is nothing to put in it, so it is not there.
		const bare = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(bare).not.toContain('aria-label="Client actions"');
	});

	it('keeps the overflow menu OUT of the corner the vulnerability banner uses', () => {
		// Both sat at the card's top-right: the kebab absolutely positioned, the banner's own Update
		// button at the end of its row. On a flagged client the kebab covered Update and clipped it to
		// "Update..". An absolutely-positioned control has left the flow, so nothing the banner does can
		// reserve space for it — the kebab is in the identity row now and takes its own.
		const body = render(ClientRecord, {
			props: {
				detail: detail({
					is_vulnerable: true,
					vulnerability_reasons: ['life_event'],
					vulnerability_is_temporary: true,
					vulnerability_recorded_at: '2026-07-30T09:00:00Z'
				}),
				links,
				// `setVulnerability` is what puts the banner's own Update button on screen — the shared
				// `actions` fixture omits it, and without it the two controls could not collide at all,
				// which would make this test pass for the wrong reason.
				actions: { ...actions, setVulnerability: '?/setVulnerability' }
			}
		}).body;
		expect(body).toContain('Client actions');
		expect(body).toContain('Vulnerable client');
		expect(body).toContain('Update');
		expect(body).not.toContain('absolute top-4 right-4');
	});

	it('shows NO screening on the record — that is Graphene’s function, not the firm’s', () => {
		// Identity and AML screening is Graphene's as platform provider: the firm sees the outcome of a
		// review, never the review (docs/aml.md), and `screening:read` already gives them a dedicated
		// page. On the record it was the FIRST card in the rail, which put our compliance process above
		// the client's own money in a firm-facing view. `screeningChecks` stays in the contract.
		const body = render(ClientRecord, {
			props: {
				detail: detail(),
				links,
				screeningChecks: [
					{
						kind: 'identity',
						status: 'passed',
						provider: 'complycube',
						verified_at: '2026-07-01T09:00:00Z',
						valid_until: null,
						updated_at: '2026-07-01T09:00:00Z'
					}
				]
			}
		}).body;
		expect(body).not.toContain('Checks are carried out by Graphene.');
		expect(body).not.toContain('>Screening<');
	});

	it('shows no Models card — the accounts table already names each account’s model', () => {
		// It listed the distinct model names across the client's accounts, which is a fact about each
		// ACCOUNT and is the Model column below — the same words twice, and the table's version says
		// which account holds which.
		const body = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(body).not.toContain('>Models<');
	});

	it("renders the app's queue INSIDE the main column, above the figures", () => {
		// It is a prompt about this client's money, which is what the main column is — and as a
		// full-width strip it pushed the whole rail down a band for no reason.
		const source = componentSource();
		const column = source.indexOf('order-4 min-w-0 space-y-6');
		const queue = source.indexOf('{@render queue?.()}');
		const figures = source.indexOf('label="Portfolio value"');
		expect(column).toBeGreaterThan(-1);
		expect(queue).toBeGreaterThan(column);
		expect(queue).toBeLessThan(figures);
		// One render only: a second would be the strip in both places.
		expect(source.split('{@render queue?.()}')).toHaveLength(2);
	});

	it('tasks render presentation-ready items; the All tasks link follows links.tasks', () => {
		const tasks = [
			{ uid: 't_1', title: 'Chase transfer forms', overdue: true, dueLabel: '3 days overdue' }
		];
		const withLink = render(ClientRecord, { props: { detail: detail(), links, tasks } }).body;
		expect(withLink).toContain('Chase transfer forms');
		expect(withLink).toContain('3 days overdue');
		expect(withLink).toContain('href="/tasks"');
		expect(withLink).toContain('href="/tasks/t_1"');

		const noLink = render(ClientRecord, {
			props: { detail: detail(), links: { ...links, tasks: null }, tasks }
		}).body;
		expect(noLink).toContain('Chase transfer forms');
		expect(noLink).not.toContain('All tasks');
	});

	it('the rail sums pending expected-in and lists the client’s models', () => {
		const body = render(ClientRecord, {
			props: {
				detail: detail(),
				links,
				instructions: [instruction(), instruction({ uid: 'ins_2', amount: 2_500 })]
			}
		}).body;
		expect(body).toContain('Expected in');
		expect(body).toMatch(/£7,500<span[^>]*>\.00<\/span>/);
		expect(body).toContain('Balanced 60/40');
	});
});

describe('employment (0126 + 0051)', () => {
	it('states the annual earnings on the pension section for a client who holds one', () => {
		const { body } = render(ClientRecord, {
			props: { detail: detail({ relevant_uk_earnings: 84_250 }), links }
		});
		// "Annual earnings" is the LABEL; `relevant_uk_earnings` is the column. The statutory name is
		// what the relief maths computes with — not everything on a P60 is relievable — and matches
		// Morgan Lloyd's `cam_annualearnings`.
		expect(body).toContain('Annual earnings');
		expect(body).toContain('£84,250');
		// Through `Money`: the pence are their own span, not part of the pounds.
		expect(body).toMatch(/£84,250<span[^>]*>\.00<\/span>/);
	});

	it('shows the employer regardless of whether employer contributions are declared', () => {
		// The Employer card no longer gates on the "Receives employer contributions" answer
		// (docs/client-record.md §3): that Switch still saves as a normal field, but the section
		// itself completes the picture rather than hiding pending an answer.
		const without = render(ClientRecord, {
			props: { detail: detail({ employer_name: 'Halloway Dental Ltd' }), links }
		}).body;
		expect(without).toContain('Employer details');
		expect(without).toContain('Halloway Dental Ltd');

		const with_ = render(ClientRecord, {
			props: {
				detail: detail({
					employer_name: 'Halloway Dental Ltd',
					employer_contributions: true,
					employer_registration_number: '00445790'
				}),
				links
			}
		}).body;
		expect(with_).toContain('Employer details');
		expect(with_).toContain('Halloway Dental Ltd');
		// TEXT, not a number: the leading zeros are significant.
		expect(with_).toContain('00445790');
	});

	it('states pension and employer facts even for a client with no pension', () => {
		// `relevant_uk_earnings` and `employer_name` used to be hidden for a GIA/ISA-only client on
		// the theory that asking for them was asking for something the firm had no business holding.
		// That gate is gone (docs/client-record.md §3): every individual's Details tab always states
		// them, with an em dash where nobody has answered.
		const d = detail({ relevant_uk_earnings: 84_250, employer_name: 'Halloway Dental Ltd' });
		const { body } = render(ClientRecord, {
			props: {
				detail: { ...d, accounts: d.accounts.filter((a) => a.product_type !== 'SIPP') },
				links
			}
		});
		expect(body).toContain('Annual earnings');
		expect(body).toContain('Halloway Dental Ltd');
	});

	it('shows the row as an em dash when the figure is not on file', () => {
		// The REVERSE of the old rule, and the reason for the restructure. Dropping the row is what
		// let this column go five migrations with no editor while the HMRC relievable-limit check
		// ran on the £3,600 floor: nobody could see it was missing.
		const { body } = render(ClientRecord, { props: { detail: detail(), links } });
		expect(body).toContain('Annual earnings');
	});

	it('offers the income as an editable field on the pension form', () => {
		// A SOURCE scan, because the details panel is view-first: the form only exists once the
		// pencil has been used, and SSR cannot reach that state (the same reason the edit-mode cases
		// above read `componentSource`).
		//
		// `relevant_uk_earnings` had a column since 0051 and NO editor anywhere — so the HMRC
		// relievable-limit check silently ran on the £3,600 floor for every client whose figure no
		// onboarding journey had set, and nobody could see the number it was running on. `employer`
		// is a MoneyInput's neighbour rather than a per-payment question for the first time.
		const source = componentSource();
		const gate = source.indexOf('{#snippet pensionBody');
		expect(gate).toBeGreaterThan(-1);
		expect(source.slice(gate)).toContain('name="relevant_uk_earnings"');
	});
});

describe('the five sections (0127)', () => {
	/** The default fixture holds an ISA and a SIPP, so it IS a pension client. */
	const noPension = (over: Partial<ClientRecordDetail['contact']> = {}): ClientRecordDetail => {
		const d = detail(over);
		return { ...d, accounts: d.accounts.filter((a) => !a.is_pension) };
	};

	it('renders each section as its own card, saving only its own fields', () => {
		// One action serves all five: the API's PATCH treats a present null as "clear" and an absent
		// key as "leave alone", so a form carrying only its own fields updates only its own. Five
		// separate actions would be five copies of the rule and five places to forget it.
		const body = render(ClientRecord, { props: { detail: detail(), links, actions } }).body;
		for (const title of ['Contact details', 'Personal details', 'Pension details', 'Compliance']) {
			expect(body, title).toContain(title);
		}
	});

	it('always titles the pension section Pension details, whatever wrapper the shelf used', () => {
		// A client can hold TPPENSION and SIPP at once. Naming the section after the first
		// pension account's wrapper made the heading a lie for everyone who holds two.
		const d = detail();
		const body = render(ClientRecord, {
			props: {
				detail: { ...d, accounts: [{ ...d.accounts[1], is_pension: true }] },
				links,
				pensionLabel: 'TPPENSION'
			}
		}).body;
		expect(body).toContain('Pension details');
		expect(body).not.toContain('TPPENSION details');
		expect(body).not.toContain('SIPP details');
	});

	it('shows the pension section even for a client who holds no pension', () => {
		// Pension and Employer used to be hidden for a client with no pension wrapper. That gate is
		// gone (docs/client-record.md §3): the record completes the picture rather than inferring
		// what is worth asking, same as every other section here.
		const body = render(ClientRecord, { props: { detail: noPension(), links } }).body;
		expect(body).toContain('Pension details');
		expect(body).toContain('Employer details');
		expect(body).toContain('Contact details');
		expect(body).toContain('Compliance');
	});

	it('offers no pencil at all on a read-only mount', () => {
		// The admin portal's mode: every section renders its register and none offers a form.
		const body = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(body).toContain('Contact details');
		expect(body).not.toContain('Edit contact details');
		expect(body).not.toContain('action="?/updateClient"');
	});

	it('states the US-person answer and the tax form separately', () => {
		const body = render(ClientRecord, {
			props: { detail: detail({ us_person: true, fatca_status: 'w9' }), links }
		}).body;
		expect(body).toContain('Is a US person?');
		expect(body).toContain('W-9 or W-8');
		expect(body).toContain('W-9');
		const notUs = render(ClientRecord, {
			props: { detail: detail({ us_person: false, fatca_status: null }), links }
		}).body;
		expect(notUs).not.toContain('Yes — W-9');
		expect(notUs).not.toContain('W-9</');
	});

	it('states the signed W-9 or W-8, linked where the app can open a document', () => {
		const signed = { uid: 'doc_w9', name: 'w9-signed.pdf' };
		const linked = render(ClientRecord, {
			props: {
				detail: detail({ us_person: true, fatca_status: 'w9', fatca_document: signed }),
				links: { ...links, document: (uid: string) => `/documents/${uid}` }
			}
		}).body;
		expect(linked).toContain('W-9 / W-8 form');
		expect(linked).toMatch(/<a href="\/documents\/doc_w9"[^>]*>w9-signed\.pdf<\/a>/);

		// No document route on this mount (the admin book): the name, without a link.
		const unlinked = render(ClientRecord, {
			props: {
				detail: detail({ us_person: true, fatca_status: 'w9', fatca_document: signed }),
				links
			}
		}).body;
		expect(unlinked).toContain('w9-signed.pdf');
		expect(unlinked).not.toContain('doc_w9');

		const missing = render(ClientRecord, {
			props: { detail: detail({ us_person: false, fatca_status: 'w8' }), links }
		}).body;
		expect(missing).toContain('W-9 / W-8 form');
		expect(missing).toContain('Not uploaded');

		for (const fatca_status of [null, 'w8_cure']) {
			const none = render(ClientRecord, {
				props: { detail: detail({ fatca_status }), links }
			}).body;
			expect(none, String(fatca_status)).not.toContain('W-9 / W-8 form');
		}
	});

	it('posts the signed W-9 or W-8 with the Compliance card’s own Save', () => {
		// The slot reads the DRAFT, so it follows the select; the file belongs to the SAVED answer.
		// Multipart, or `enhance` sends the form urlencoded and the file never leaves the browser.
		const source = componentSource();
		const start = source.indexOf('{#snippet complianceBody');
		const body = source.slice(start, start + source.slice(start).indexOf('{/snippet}'));
		expect(body).toMatch(/<form\s+method="POST"\s+enctype="multipart\/form-data"/);
		expect(body).toContain('<FatcaDocumentField');
		expect(body).toContain('status={fatcaStatusDraft}');
		expect(body).toContain('savedStatus={c.fatca_status}');
		expect(body.indexOf('<FatcaDocumentField')).toBeGreaterThan(
			body.indexOf('name="fatca_status"')
		);
		expect(body.indexOf('<FatcaDocumentField')).toBeLessThan(body.indexOf('{@render saveBar()}'));
	});

	it('states the compliance ticks, and "not recorded" is not a failure', () => {
		// A red cross against a check nobody has started reads as a failure. The three are read from
		// `crm.kyc_checks` and are deliberately NOT editable here: an adviser ticking "AML done" by
		// hand would put an unevidenced clearance on a regulated record.
		const body = render(ClientRecord, {
			props: {
				detail: {
					...detail(),
					kyc: [{ kind: 'identity', status: 'passed', verified_at: '2026-07-01' }]
				},
				links
			}
		}).body;
		expect(body).toContain('Identity verified');
		expect(body).toContain('AML screened');
		expect(body).toContain('Terms accepted');
		expect(body).toContain('Not recorded');
	});

	it('offers source of wealth as Morgan Lloyd’s closed list with a supporting statement', () => {
		// `_SOURCE_OF_WEALTH` sends their INT only on an exact label match, so a picker is what
		// makes the mapping hit. Other is the one member that means nothing without the words.
		expect([...SOURCE_OF_WEALTH_OPTIONS]).toEqual([
			'Salary',
			'Variable Remuneration',
			'Investment Income or Sale of Investments',
			'Pension Income',
			'Sale of Main Residence',
			'Sale of Other Property',
			'Business Interests',
			'Inheritence',
			'Gift',
			'Financial Settlements',
			'Lottery/Betting/Casino',
			'Other'
		]);
		const source = componentSource();
		expect(source).toContain('bind:value={sourceOfWealthDraft}');
		expect(source).toContain('bind:value={sourceOfWealthDetailDraft}');
		expect(source).toContain("items={[{ value: '', label: '—' }, ...sourceOfWealthChoices]}");
		expect(source).toContain('name="source_of_wealth_detail"');
		expect(source).toContain('minlength={30}');
		const personalStart = source.indexOf('{#snippet personalBody');
		const personalBody = source.slice(personalStart, source.indexOf('{/snippet}', personalStart));
		const complianceStart = source.indexOf('{#snippet complianceBody');
		const complianceBody = source.slice(
			complianceStart,
			source.indexOf('{/snippet}', complianceStart)
		);
		expect(complianceBody).toContain('name="source_of_wealth"');
		expect(personalBody).not.toContain('name="source_of_wealth"');
		const body = render(ClientRecord, {
			props: {
				detail: detail({
					source_of_wealth: 'Investment Income or Sale of Investments'
				}),
				links
			}
		}).body;
		expect(body).toContain('Investment Income or Sale of Investments');
		const coded = render(ClientRecord, {
			props: { detail: detail({ source_of_wealth: 'employment_income' }), links }
		}).body;
		expect(coded).toContain('Employment income');
		expect(coded).not.toContain('employment_income');
	});

	it('asks for occupation detail only for Other and Retired', () => {
		// A named job is the whole answer. Other needs the words; Retired needs what they did
		// before — one field, two labels, hidden for every other pick.
		const source = componentSource();
		expect(source).toContain("occupationDraft === 'Other'");
		expect(source).toContain("occupationDraft === 'Retired'");
		expect(source).toContain('Previous occupation');
		expect(source).toContain('bind:value={occupationDraft}');
		expect(source).not.toContain('Occupation detail');
		const personalStart = source.indexOf('{#snippet personalBody');
		const personalBody = source.slice(personalStart, source.indexOf('{/snippet}', personalStart));
		const complianceStart = source.indexOf('{#snippet complianceBody');
		const complianceBody = source.slice(
			complianceStart,
			source.indexOf('{/snippet}', complianceStart)
		);
		expect(personalBody).toContain('name="occupation"');
		expect(complianceBody).not.toContain('name="occupation"');
		expect(complianceBody).not.toContain("['Occupation'");
	});

	it('maps Postcoder’s `town` onto our `city`, in one place', () => {
		// `Address` has no `city` key and our column has no `town`, so a naive lookup returned
		// undefined for exactly one field: the picked address filled everything EXCEPT the town,
		// which reads as Postcoder being flaky rather than as a key name. Asserted against the
		// normaliser's real output so the two cannot drift.
		const picked = normaliseAddress({
			addressline1: '10 Downing Street',
			posttown: 'London',
			postcode: 'SW1A 2AA',
			country: 'United Kingdom',
			countrycode: 'GB'
		});
		expect(picked.town).toBe('London');
		// `as unknown as`: `Address` has no index signature, so a direct assertion to a record is
		// the conversion TypeScript refuses — and the point here is exactly that the key is absent.
		expect((picked as unknown as Record<string, unknown>).city).toBeUndefined();
		// And the record maps it rather than reading `city` off the pick.
		expect(componentSource()).toContain("city: 'town'");
	});

	it('labels nationalities by adjective and derives the identifier from them', () => {
		// The regulator decides which identifier a person must give, and decides it from their
		// NATIONALITY — so the type is not a text box an adviser types "NINO" into.
		const source = componentSource();
		expect(source).toContain('nationalityOptions(countries)');
		expect(source).toContain('taxResidencies.length ? taxResidencies : nationalities');
		// Posted as a hidden field, because it is the regulator's choice and not the adviser's.
		expect(source).toContain('name="national_identifier_type"');
		expect(source).toMatch(/type="hidden"[\s\S]{0,80}national_identifier_type/);
		// CONCAT is computed off the form's own name and date fields, so it follows a correction.
		expect(source).toContain('computeConcat({ dob: dobDraft');
	});

	it('edits the US-person answer and source of funds on Compliance', () => {
		const source = componentSource();
		const complianceStart = source.indexOf('{#snippet complianceBody');
		const complianceBody = source.slice(
			complianceStart,
			source.indexOf('{/snippet}', complianceStart)
		);
		const personalStart = source.indexOf('{#snippet personalBody');
		const personalBody = source.slice(personalStart, source.indexOf('{/snippet}', personalStart));
		expect(complianceBody).toContain('Is a US person?');
		expect(complianceBody).toContain('name="us_person"');
		expect(complianceBody).toContain('name="source_of_funds"');
		expect(complianceBody).not.toContain("['Country of birth'");
		expect(personalBody).toContain('name="country_of_birth"');
		expect(personalBody).not.toContain('name="us_person"');
		expect(personalBody).not.toContain('name="source_of_funds"');
		const body = render(ClientRecord, {
			props: { detail: detail({ source_of_funds: 'Personal savings' }), links }
		}).body;
		expect(body).toContain('Source of funds');
		expect(body).toContain('Personal savings');
	});

	it('does not put a passport on Compliance or Personal details', () => {
		// Nationality and tax residency already live on Personal. A passport number is an SEI
		// enrolment fact, not something an adviser fills in on this card.
		expect(componentSource()).not.toMatch(/name="passport_/);
	});

	it('keeps the facts it does not edit above the save bar', () => {
		// Compliance is the only section that shows a form AND a register at once (the ticks and the
		// US-person declaration nobody edits here have to stay visible), and the register used to
		// follow the form — so opening the pencil put those facts underneath Save/Cancel, reading
		// as having dropped out of the card.
		const source = componentSource();
		const start = source.indexOf('{#snippet complianceBody');
		const body = source.slice(start, start + source.slice(start).indexOf('{/snippet}'));
		const registerCall = body.indexOf('{@render register(facts');
		const saveBarCall = body.indexOf('{@render saveBar()}');
		expect(registerCall).toBeGreaterThan(-1);
		expect(saveBarCall).toBeGreaterThan(-1);
		expect(registerCall).toBeLessThan(saveBarCall);
	});

	it('offers a pencil on Compliance for source of wealth, not the ticks', () => {
		const source = componentSource();
		expect(source).toMatch(
			/sectionCard\(\s*'compliance',\s*'Compliance',\s*complianceBody,\s*null,\s*!isOrg,/
		);
		expect(source).not.toMatch(
			/sectionCard\(\s*'compliance',\s*'Compliance',\s*complianceBody,\s*null,\s*false/
		);
		const body = render(ClientRecord, { props: { detail: detail(), links, actions } }).body;
		expect(body).toContain('Edit compliance');
		const readOnly = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(readOnly).not.toContain('Edit compliance');
		const org = render(ClientRecord, {
			props: { detail: detail({ type: 'organisation', name: 'Caldwell Trust' }), links, actions }
		}).body;
		expect(org).not.toContain('Edit compliance');
	});

	it('keeps the person-facts off an organisation’s Compliance card', () => {
		// Not a US person / source of wealth / place of birth are facts about a human. A trust
		// still has the identity / AML / terms ticks — those are the file, not the person.
		const body = render(ClientRecord, {
			props: {
				detail: detail({
					type: 'organisation',
					name: 'Caldwell Trust',
					us_person: false,
					source_of_wealth: 'Gift',
					country_of_birth: 'GB',
					town_of_birth: 'Bath'
				}),
				links,
				actions
			}
		}).body;
		expect(body).toContain('Identity verified');
		expect(body).not.toContain('Not a US person');
		expect(body).not.toContain('Source of wealth');
		expect(body).not.toContain('Country of birth');
		expect(body).not.toContain('Town of birth');
		expect(body).not.toContain('Gift');
	});

	it('does not offer copy on an empty email, mobile or address', () => {
		const body = render(ClientRecord, {
			props: {
				detail: detail({ email: null, mobile: null, address: null }),
				links
			}
		}).body;
		expect(body).toContain('No email');
		expect(body).toContain('No mobile');
		expect(body).toContain('No address');
		expect(body).not.toContain('aria-label="Copy email"');
		expect(body).not.toContain('aria-label="Copy mobile"');
		expect(body).not.toContain('aria-label="Copy address"');
	});

	it('shows no empty state at all for zero notes — just the composer, nothing else', () => {
		const empty = { ...detail(), notes: [] };
		const body = render(ClientRecord, { props: { detail: empty, links, actions } }).body;
		expect(body).not.toContain('No notes yet');
		expect(body).not.toContain('Add the first note about this client above.');
		expect(body).not.toContain('Notes about this client appear here.');
	});

	it('prints book cost and unrealised gain when the loader supplies them', () => {
		const body = render(ClientRecord, {
			props: {
				detail: detail(),
				links,
				bookCost: 800_000,
				unrealisedGain: 12_500
			}
		}).body;
		expect(body).toMatch(/£800,000<span class="[^"]*text-muted-foreground[^"]*">\.00<\/span>/);
		expect(body).toMatch(/\+£12,500<span class="[^"]*opacity-70[^"]*">\.00<\/span>/);
		expect(body).toContain('Book cost');
		expect(body).toContain('Unrealised gain / loss');
	});

	it('hides the bank pencil when every account refuses withdrawal bank details', () => {
		const d = detail();
		const jisaOnly = {
			...d,
			accounts: d.accounts.map((a) => ({
				...a,
				product_type: 'JISA',
				bank_details_allowed: false
			}))
		};
		const body = render(ClientRecord, { props: { detail: jisaOnly, links, actions } }).body;
		expect(body).toContain('none of this client’s products accept withdrawals');
		expect(body).not.toContain('Edit bank details');
	});

	it('overview keeps personal details and accounts; details keeps the person cards', () => {
		const overview = render(ClientRecord, {
			props: { detail: detail(), links, actions, section: 'overview' }
		}).body;
		expect(overview).toContain('Personal details');
		expect(overview).not.toContain('Edit personal details');
		expect(overview).not.toContain('>Contact details<');
		expect(overview).not.toContain('>Pension details<');
		expect(overview).not.toContain('>Bank details<');
		expect(overview).toContain('Portfolio value');
		expect(overview).toContain('>ISA<');

		const detailsView = render(ClientRecord, {
			props: { detail: detail(), links, actions, section: 'details' }
		}).body;
		expect(detailsView).toContain('Contact details');
		expect(detailsView).toContain('Personal details');
		expect(detailsView).toContain('Edit personal details');
		expect(detailsView).toContain('Edit bank details');
		expect(detailsView).toContain('Pension details');
		expect(detailsView).toContain('Employer details');
		expect(detailsView).not.toContain('Portfolio value');
		expect(detailsView).not.toContain('>ISA<');
		expect(detailsView).toContain('Team');
		expect(detailsView).toContain('Compliance');
		expect(detailsView).toContain('Notes');
	});

	it('expands Team to a named list with the adviser tag, and keeps the stack when shut', () => {
		const body = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(body).toContain('Team');
		expect(body).toContain('Zoe Lead');
		expect(body).toContain('Partner');
		expect(body).toContain('>Adviser<');
		expect(body).toContain('aria-label="Team actions"');
	});

	it('tags the adviser by ROLE, first, and names everyone else by theirs', () => {
		// The API sends the adviser first; the record does not rely on it. Mia may advise (the
		// staff flag) but is Support on THIS client, so she must not wear the Adviser tag.
		const team = {
			...detail(),
			advisers: [
				{ name: 'Mia Support', email: 'mia@firm.test', is_adviser: true, role: 'support' },
				{ name: 'Pat Para', email: 'pat@firm.test', is_adviser: false, role: 'paraplanner' },
				{ name: 'Zoe Lead', email: 'zoe@firm.test', is_adviser: true, role: 'adviser' }
			]
		};
		const body = render(ClientRecord, { props: { detail: team, links } }).body;
		expect(body.match(/>Adviser</g)).toHaveLength(1);
		expect(body).toContain('>Support<');
		expect(body).toContain('>Paraplanner<');
		const zoe = body.indexOf('Zoe Lead');
		expect(zoe).toBeGreaterThan(-1);
		expect(zoe).toBeLessThan(body.indexOf('Mia Support'));
		expect(zoe).toBeLessThan(body.indexOf('Pat Para'));
	});

	it('offers the team’s roles from one list, never a free-text role', () => {
		const source = componentSource();
		expect(source).toContain('items={CLIENT_TEAM_ROLE_ITEMS}');
		expect(source).not.toContain('placeholder="lead"');
		expect(source).not.toMatch(/<Input[^>]*name="role"/);
	});
});

describe('vulnerability (FCA FG21/1)', () => {
	const flagged = () =>
		detail({
			is_vulnerable: true,
			vulnerability_reasons: ['life_event', 'resilience'],
			vulnerability_is_temporary: true,
			vulnerability_recorded_at: '2026-07-30T09:00:00Z',
			vulnerability_notes: 'Recently bereaved; agreed to post statements.'
		});

	it('states the flag, every driver and the permanence on the identity header', () => {
		// All applicable drivers, not a primary one: FG21/1 is explicit that they compound, and the
		// adviser is meant to see the whole picture without opening anything.
		const body = render(ClientRecord, { props: { detail: flagged(), links } }).body;
		expect(body).toContain('Vulnerable client');
		expect(body).toContain('Life event');
		expect(body).toContain('Resilience');
		expect(body).toContain('Temporary');
		expect(body).toContain('Recently bereaved');
		// The whole card is re-tinted rather than growing a banner: you cannot read the name
		// without reading the flag.
		expect(body).toContain('bg-destructive-subtle');
	});

	it('keeps line breaks in the banner notes', () => {
		// The drawer is a textarea; flattening the saved text on the header made a two-line
		// adjustment look like one run-on sentence. `whitespace-pre-wrap` only works if the
		// newline is still in the markup — the notes list pins the same contract.
		const body = render(ClientRecord, {
			props: {
				detail: detail({
					is_vulnerable: true,
					vulnerability_reasons: ['life_event'],
					vulnerability_notes: 'Recently bereaved.\nPrefers post.'
				}),
				links
			}
		}).body;
		expect(body).toMatch(/Recently bereaved\.\s*\n\s*Prefers post\./);
		const banner = readFileSync(join(import.meta.dirname, 'vulnerability-banner.svelte'), 'utf8');
		expect(banner).toContain('whitespace-pre-wrap');
	});

	it('says nothing at all when the client is not flagged', () => {
		const body = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(body).not.toContain('Vulnerable client');
		expect(body).not.toContain('bg-destructive-subtle');
	});

	it('states the FG21/1 characteristics beneath the drivers (0116)', () => {
		// The detail that supports the judgement. Shown on the banner because an adviser has to be
		// able to SEE what was recorded — a control that writes and never reads back is how a
		// record ends up quietly less true than the conversation it came from.
		const body = render(ClientRecord, {
			props: {
				detail: detail({
					is_vulnerable: true,
					vulnerability_reasons: ['life_event', 'resilience'],
					vulnerability_characteristics: ['bereavement', 'low_emotional_resilience']
				}),
				links
			}
		}).body;
		expect(body).toContain('Bereavement');
		expect(body).toContain('Low emotional resilience');
	});

	it('renders the characteristics as text, not as twenty-four more badges', () => {
		// The drivers are the signal and carry the badges; a row of a dozen destructive badges
		// would compete with the four that mean something.
		const body = render(ClientRecord, {
			props: {
				detail: detail({
					is_vulnerable: true,
					vulnerability_reasons: ['health'],
					vulnerability_characteristics: ['visual_impairment']
				}),
				links
			}
		}).body;
		const badges = body.split('Visual impairment')[0].slice(-260);
		expect(badges).not.toContain('badge');
	});

	it('shows no characteristics line when none was recorded', () => {
		// Optional where a driver is required: a flag raised in a first conversation often has the
		// driver before it has the specifics, and an empty line would read as a missing answer.
		const body = render(ClientRecord, {
			props: {
				detail: detail({
					is_vulnerable: true,
					vulnerability_reasons: ['health'],
					vulnerability_characteristics: []
				}),
				links
			}
		}).body;
		expect(body).toContain('Vulnerable client');
		expect(body).not.toContain('Visual impairment');
	});

	it('draws no flag when the block was never fetched', () => {
		// The clients LISTING omits the vulnerability columns, so `undefined` must read as "not
		// known here" and not as "not vulnerable" — a surface that has not asked must not answer.
		const body = render(ClientRecord, {
			props: { detail: detail({ is_vulnerable: undefined }), links }
		}).body;
		expect(body).not.toContain('Vulnerable client');
	});

	it('is read-only without the action: the flag shows, the form does not', () => {
		// The admin book's posture — ops must SEE that a client is vulnerable (it governs any
		// contact they have) while only the firm records it. The details card states the same
		// facts; the pencil is the firm's.
		const body = render(ClientRecord, { props: { detail: flagged(), links } }).body;
		expect(body).toContain('Vulnerable client');
		expect(body).toContain('id="vulnerability"');
		expect(body).not.toContain('?/setVulnerability');
		expect(body).not.toContain('Record vulnerability');
		expect(body).not.toContain('Edit vulnerability');
	});

	it('files the record on the details cards', () => {
		const body = render(ClientRecord, {
			props: {
				detail: flagged(),
				links,
				section: 'details',
				actions: { enhance, setVulnerability: '?/setVulnerability' }
			}
		}).body;
		expect(body).toContain('id="vulnerability"');
		expect(body).toContain('>Vulnerability<');
		// ONCE. Two branches each filed a Vulnerability card on the same day and the merge kept
		// both, so Details stated the flag as banner, card and card again.
		expect(body.match(/>Vulnerability</g)).toHaveLength(1);
		expect(body).toContain('Edit vulnerability');
		expect(body).toContain('Life event');
		expect(body).toContain('Resilience');
		expect(body).toContain('Temporary');
		expect(body).toContain('Recently bereaved');
		// Unflagged still has the card — a section a firm is meant to complete has to show
		// its own gap — and does not invent the header banner.
		const clear = render(ClientRecord, {
			props: { detail: detail(), links, section: 'details' }
		}).body;
		expect(clear).toContain('id="vulnerability"');
		expect(clear).not.toContain('Vulnerable client');
		expect(clear).not.toContain('Edit vulnerability');
	});

	it('offers Record vulnerability only while there is nothing to state', () => {
		// The trigger moved into the header's overflow menu, whose content a closed DropdownMenu does
		// not render at SSR — so what is asserted here is the MENU's presence, and the drawer's form
		// behind it, rather than the item's label. (Same reason the documents kebab's order is read off
		// source: a closed panel is invisible to these tests.)
		const withVuln: ClientRecordActions = { enhance, setVulnerability: '?/setVulnerability' };
		const unflagged = render(ClientRecord, {
			props: { detail: detail(), links, actions: withVuln }
		}).body;
		expect(unflagged).toContain('Client actions');
		// The item's label and its drawer are both behind the closed menu — a DropdownMenu renders no
		// content at SSR, and the sheet only mounts once opened. So the ITEM is read off source; the
		// rendered body proves only that the menu is there to hold it.
		const source = componentSource();
		expect(source).toContain('Record vulnerability');

		// Once flagged, the strip owns it — a second entry point would be the same control twice.
		const already = render(ClientRecord, {
			props: { detail: flagged(), links, actions: withVuln }
		}).body;
		expect(already).not.toContain('Record vulnerability');
		expect(already).toContain('Update');
	});

	it('is gated on its own action name, not on updateClient', () => {
		// It has its own validated endpoint and its own audit row, so a role that can edit the
		// details form does not thereby get to record special-category-adjacent data.
		const detailsOnly: ClientRecordActions = { enhance, updateClient: '?/updateClient' };
		const body = render(ClientRecord, {
			props: { detail: flagged(), links, actions: detailsOnly }
		}).body;
		expect(body).toContain('Edit contact details');
		// The TRIGGER, not the form: the drawer is closed on first render, so asserting the action
		// name is absent would pass whether or not it was passed. The details pencil is the same
		// gate: `updateClient` does not record this.
		expect(body).not.toContain('>Update<');
		expect(body).not.toContain('Edit vulnerability');
	});
});

describe('standing arrangements', () => {
	it('lists regular payments and withdrawals across every account', () => {
		const withStanding = detail();
		withStanding.accounts = [
			{
				...withStanding.accounts[0]!,
				withdrawals: [
					{ amount: 100, payment_day: 14, method: 'BACS Credit', auto_disinvest: 'Largest' }
				]
			},
			{
				...withStanding.accounts[1]!,
				payments: [{ amount: 250, payment_day: 1, method: 'Direct Debit' }]
			}
		];
		const body = render(ClientRecord, { props: { detail: withStanding, links } }).body;
		expect(body).toContain('id="standing"');
		expect(body).toContain('Regular payments');
		expect(body).toContain('Withdrawal');
		expect(body).toContain('Payment');
		expect(body).toContain('14th of every month');
		expect(body).toContain('href="/tenants/t_1/book/accounts/acc_7"');
	});

	it('stays quiet when no account has an arrangement', () => {
		const body = render(ClientRecord, { props: { detail: detail(), links } }).body;
		expect(body).not.toContain('id="standing"');
	});
});
