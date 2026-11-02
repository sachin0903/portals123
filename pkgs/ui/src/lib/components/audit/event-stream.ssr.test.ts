/**
 * SSR pins for the EventStream (the full-width action-log with rows that expand in place): the
 * ACTOR-led row leads with a category glyph + initials disc, the raw event type chip stays visible,
 * a proxied row announces where the operator went, and the DETAIL reads CHANGES-first (old → new
 * rows, ± permission chips) with the Metadata below. The empty state distinguishes "nothing yet"
 * from "nothing matches".
 *
 * The detail IS in the table — it was a side pane at 58%/42% and is now an expanded row under its
 * own line. NOTHING is open on arrival, so these render the row and the detail separately; see
 * `opened` below for why that is the honest shape rather than a lost assertion.
 */
import { describe, expect, it } from 'vitest';
import type { ComponentProps } from 'svelte';
import { readFileSync } from 'node:fs';
import { render } from 'svelte/server';
import EventStream, { EMPTY_ACTION_LOG_FILTERS } from './event-stream.svelte';
import EventDetail from './event-detail.svelte';
import type { ActionEvent } from './describe.js';

const event = (over: Partial<ActionEvent> = {}): ActionEvent => ({
	id: 1,
	event_type: 'client.created',
	created_at: '2026-08-03T09:15:00Z',
	method: 'POST',
	route: '/firm/contacts',
	status: 200,
	path_params: {},
	metadata: { correlation_id: 'corr_1' },
	actor_id: 'u-1',
	actor_sub: 'wos_alice',
	actor_email: 'alice@firm.test',
	actor_name: 'Alice Feed',
	tenant_id: 1,
	tenant_uid: 'ten_firm',
	tenant_name: 'Feed Firm',
	target_tenant_id: null,
	target_tenant_uid: null,
	target_tenant_name: null,
	...over
});

// `events` is the only REQUIRED prop — a mount with no fetcher narrows the loaded rows instead — so
// none of these row/detail cases needs a callback. The filter FETCHING is pinned in
// event-stream.filtering.ssr.test.ts, beside the applier it uses. Kept as the one place every case
// reads its shared props from.
const base = {};

// The detail no longer carries a raw-JSON section, so the whole body IS the humanised view. Kept as
// a named helper because the cases below read as "withheld from what a person sees".
const humanised = (body: string) => body;

/** Just the `<table>` — the detail is inside it now, and the TOOLBAR above it is full of bits-ui
 *  triggers that carry `aria-expanded` of their own. Counting disclosure state document-wide would
 *  count the filter dropdowns as open rows. */
const tableOf = (body: string) =>
	body.slice(body.indexOf('<table'), body.indexOf('</table>') + '</table>'.length);

/**
 * The stream, and the detail of the row a reader opens on it.
 *
 * The stream used to unfold its newest row on arrival, which is what let these cases assert the
 * detail straight off an SSR render. It no longer does: a log that lands with one row already open
 * reads as "this one is special" when all it means is "this one is last". Server-rendering the
 * stream therefore yields rows and no detail.
 *
 * The pins below are about what the DETAIL SAYS, so each renders the stream and then the detail of
 * the row it opens — with the props the stream's own expanded cell passes it (`variant="inline"`,
 * the same `links` and `entityHref`). `body` is both, for "the surface says this"; `detail` is the
 * panel alone, for "the PANEL says this and the row does not", which a whole-body scan cannot tell
 * apart. That a click puts THIS component in THAT cell is pinned in event-stream.expand.dom.test.ts,
 * where a real DOM can do the clicking.
 */
const opened = (opts: { props: ComponentProps<typeof EventStream> }) => {
	const body = render(EventStream, opts).body;
	const { events, links, entityHref } = opts.props;
	const first = events?.[0];
	const detail = first
		? render(EventDetail, {
				props: { event: first, variant: 'inline' as const, links, entityHref }
			}).body
		: '';
	return { body: `${body}${detail}`, detail };
};

const stripComments = (code: string) =>
	code
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/(?<!:)\/\/[^\n]*/g, '');

describe('EventStream (SSR) — the detail pane states the event, not the record', () => {
	// A reader opens an event to read what happened, and every fact here is one line long. It was a
	// wall of line-numbered JSON; then two collapsibles, which is a section heading per two rows and
	// two clicks to read six facts. Now: one flat list, nothing hidden.
	it('states every fact in one list, with nothing behind a click', () => {
		const { body, detail: panel } = opened({ props: { ...base, events: [event()] } });
		// NOTHING is collapsible — a handful of one-line facts are not worth a disclosure.
		expect(body.match(/<details/g) ?? []).toHaveLength(0);
		// What the panel states is what the ROW DOES NOT. It used to reopen with the actor, the
		// sentence, the outcome, the time and the raw event type — five facts the reader had just
		// read. The actor's EMAIL survives, because the row names the person and not their address.
		expect(panel).toContain('>Actor<');
		expect(panel).toContain('alice@firm.test');
		expect(panel).toContain('>Operation<');
		// …and the repeats are gone.
		for (const label of ['>When<', '>Type<']) {
			expect(panel, label).not.toContain(label);
		}
		expect(panel).not.toContain(event().event_type);
		// The correlation id is NOT shown: a request-tracing id for an engineer chasing a support
		// ticket, which spent a row and a copy button saying nothing to a reader of the log.
		expect(body).not.toContain('>Correlation<');
		// The raw event JSON is gone — it was a wall nobody opened an event to read.
		expect(body).not.toContain('font-mono text-[11px] leading-relaxed');
	});

	it('does not say again what the row it opened out of already says', () => {
		// THE COMPLAINT THIS ANSWERS, stated as a rule: the panel opens directly under its own row,
		// so the actor-led sentence and the outcome badge in it were the same words one line up.
		const panel = opened({ props: { ...base, events: [event()] } }).detail;
		expect(panel).not.toContain('Created a client');
		expect(panel).not.toContain('Succeeded');
	});

	it('names the record it acted on as a fact, beside the actor', () => {
		// THE SECOND HALF OF THE COMPLAINT. The panel showed `uid nrule_8f0c…` above Actor and
		// Operation. A reader opens an event to find out WHICH notice was changed, so the record's
		// NAME belongs in the same grid. The uid becomes the label's title.
		const panel = opened({
			props: {
				...base,
				events: [
					event({
						event_type: 'notification_rule.updated',
						method: 'PUT',
						route: '/firm/notification-rules/{uid}',
						path_params: { uid: 'nrule_8f0c' },
						entities: [
							{
								kind: 'notification_rule',
								id: 'nrule_8f0c',
								uid: 'nrule_8f0c',
								label: 'Document ready'
							}
						]
					})
				]
			}
		}).detail;
		// The KIND pill, then the record's NAME — and the uid is NOT printed. It rides on the row's
		// `title`, so the one reader matching a support ticket still has it and nobody else has to
		// read 32 characters of hex to find out which notice changed.
		expect(panel).toContain('Notification rule');
		expect(panel).toContain('Document ready');
		expect(panel).toMatch(/title="nrule_8f0c"/);
		expect(panel).not.toMatch(/>nrule_8f0c</);
	});

	it('states the raw id when nothing could name the record', () => {
		// NOTHING IS TIDIED AWAY. An entity the API could not name leaves its id as the only thing
		// the stream holds about what was touched, and with the route beside it that is what it would
		// take to replay the request.
		const panel = opened({
			props: {
				...base,
				events: [
					event({
						event_type: 'notification_rule.updated',
						method: 'PUT',
						route: '/firm/notification-rules/{uid}',
						path_params: { uid: 'nrule_8f0c' },
						entities: [{ kind: 'notification_rule', id: 'nrule_8f0c', uid: null, label: null }]
					})
				]
			}
		}).detail;
		expect(panel).toContain('nrule_8f0c');
	});

	it('states every path param, routable or not', () => {
		// The rule the panel is written to: everything the event RECORDED is on it. An earlier cut
		// dropped an id-shaped param that the app could not link, which read as tidier and quietly
		// removed a detail the trail exists to keep.
		const props = {
			...base,
			events: [
				event({
					event_type: 'journey.step_actioned',
					method: 'POST',
					route: '/applications/{exec_uid}/action',
					path_params: { exec_uid: 'exec_77', step_key: 'kyc' }
				})
			]
		};
		const panel = opened({ props }).detail;
		expect(panel).toContain('exec_77');
		expect(panel).toContain('kyc');
		// …and one the app CAN route becomes a link rather than disappearing.
		const linked = opened({
			props: { ...props, links: (k: string) => (k === 'exec_uid' ? '/journeys/exec_77' : null) }
		}).detail;
		expect(linked).toContain('href="/journeys/exec_77"');
	});

	it('states the record and the actor in the SAME grammar', () => {
		// THE COMPLAINT: the entity was a pill and a name while Actor and Operation were labelled
		// facts, so one panel asked the eye to read two grammars down it. Both are `<dt>`/`<dd>`
		// cells of one grid now.
		const panel = opened({
			props: {
				...base,
				events: [
					event({
						event_type: 'notification_rule.updated',
						method: 'PUT',
						route: '/firm/notification-rules/{uid}',
						path_params: { uid: 'nrule_8f0c' },
						entities: [
							{
								kind: 'notification_rule',
								id: 'nrule_8f0c',
								uid: 'nrule_8f0c',
								label: "When an account's model changes"
							}
						]
					})
				]
			}
		}).detail;
		const dl = panel.slice(panel.indexOf('<dl'), panel.indexOf('</dl>'));
		expect(dl).toContain('Notification rule');
		expect(dl).toContain("When an account's model changes");
		expect(dl).toContain('>Actor<');
		expect(dl).toContain('>Operation<');
		// The record leads: a reader opens an event to find out WHICH record it touched.
		expect(dl.indexOf('Notification rule')).toBeLessThan(dl.indexOf('>Actor<'));
	});

	it('states the recorded request field by field, not as a blob', () => {
		// Body capture is what makes an act whose content is free text or a form's answers readable
		// at all — but a reader opens an event for "Surname: Cole", not for JSON. The changed-key list
		// is suppressed when the body is present, which says the same and more.
		const panel = opened({
			props: {
				...base,
				events: [
					event({
						event_type: 'client.updated',
						method: 'PATCH',
						route: '/firm/contacts/{contact_uid}',
						path_params: { contact_uid: 'c1' },
						metadata: {
							correlation_id: 'c',
							changed: ['surname', 'password'],
							body: { surname: 'Cole', password: '[redacted]' }
						}
					})
				]
			}
		}).detail;
		expect(panel).toContain('Surname');
		expect(panel).toContain('Cole');
		// A credential is shown as having been SET and never as what it was — the API redacts it on
		// the way in, and the panel prints exactly what it was given.
		expect(panel).toContain('[redacted]');
		// …and the field-name list does not repeat what the body already says.
		expect(panel).not.toContain('Fields changed');
	});

	it('lays the facts out across the width rather than down one column', () => {
		// The surface is full width now. A single stacked column left most of the screen empty and
		// made the panel taller than the event it describes; the house fact-grid (the account
		// record's info card) states them side by side.
		const panel = opened({ props: { ...base, events: [event()] } }).detail;
		expect(panel).toMatch(/<dl[^>]*class="[^"]*grid-cols-2[^"]*lg:grid-cols-4/);
	});

	it('states the actor by EMAIL, not by the WorkOS subject nobody can act on', () => {
		const body = opened({ props: { ...base, events: [event()] } }).body;
		expect(body).toContain('alice@firm.test');
	});

	it('makes every record it can route CLICKABLE, by its own name', () => {
		// There was an "Open …" BUTTON on the record the event acted on. It restated a kind the label
		// beside it already gave, and forced the target to be plain text so the button could carry the
		// link — so the one record a reader most wanted was the one that did not look clickable.
		const { detail } = opened({
			props: {
				...base,
				events: [
					event({
						event_type: 'document.viewed',
						route: '/firm/documents/{document_uid}',
						method: 'GET',
						entities: [
							{ kind: 'contact', id: '12', uid: 'con_a', label: 'Margaret Cole' },
							{ kind: 'document', id: 'doc_x', uid: 'doc_x', label: '2026 suitability report' }
						]
					})
				],
				entityHref: (_e: ActionEvent, ent: { kind: string; uid?: string | null }) =>
					ent.uid ? `/${ent.kind}s/${ent.uid}` : null
			}
		});
		const panel = detail;
		// The document the event was ABOUT is a link, on its name.
		expect(panel).toMatch(/<a[^>]*href="\/documents\/doc_x"[^>]*>2026 suitability report<\/a>/);
		// …and so is the client it mentions: both are records this portal can open, and which one
		// the reader wants is theirs to decide.
		expect(panel).toMatch(/<a[^>]*href="\/contacts\/con_a"[^>]*>Margaret Cole<\/a>/);
		// No button, and no "Open <kind>" restating the label next to it.
		expect(panel).not.toContain('Open document');
		expect(panel).not.toContain('Open client');
		// …and it invites the click the way every clickable name in the book tables does:
		// `LINK_RECORD` — no rest-state colour and NO PERMANENT UNDERLINE, the brand colour and the
		// underline arriving together under the cursor. The same client name should not look like
		// one thing in the clients listing and another in its own audit trail.
		expect(panel).toMatch(/<a[^>]*class="[^"]*hover:text-brand[^"]*hover:underline/);
		expect(panel).not.toMatch(/<a[^>]*class="[^"]*(?<!hover:)underline /);
	});

	it('remounts details when either host moves to a different event', () => {
		// The two hosts reach the same guarantee by different means, so they are pinned separately.
		// event-feed keeps ONE pane fed by a changing `selected`, so the instance is reused with new
		// props and a stale `$derived` could survive: it needs the explicit key. event-stream expands
		// in place, where the detail block exists only while its row is open — so the remount is
		// STRUCTURAL and a `{#key}` there would be a no-op dressed as a guarantee.
		const feed = readFileSync(new URL('./event-feed.svelte', import.meta.url), 'utf8');
		expect(feed).toMatch(/\{#key selected\.id\}[\s\S]*?<EventDetail/);

		// event-stream expands in place: the detail lives in the expanded row's own block, which
		// exists only while that row is open. Opening another row destroys one block and builds the
		// other, so the remount is STRUCTURAL and a `{#key}` there would be a no-op dressed as a
		// guarantee.
		const stream = readFileSync(new URL('./event-stream.svelte', import.meta.url), 'utf8');
		expect(stream).toMatch(/\{#snippet detail\(e\)\}[\s\S]*?<EventDetail/);
		// Comments stripped FIRST: the component's own comment says `{#key}` to explain why there
		// isn't one, and a file-wide scan would read that as the thing it forbids.
		expect(stripComments(stream)).not.toContain('{#key');
	});

	it('offers no button when the host app can route nothing on the row', () => {
		const body = opened({
			props: {
				...base,
				events: [
					event({ entities: [{ kind: 'contact', id: '12', uid: 'con_a', label: 'Margaret Cole' }] })
				],
				entityHref: () => null
			}
		}).body;
		expect(body).not.toContain('Open ');
	});
});

describe('EventStream (SSR)', () => {
	it('leads actor-first with the glyph, initials, who/action split, type chip and firm chip', () => {
		const body = opened({ props: { ...base, events: [event()] } }).body;
		expect(body).toContain('Alice Feed'); // WHO
		expect(body).toContain('created a client'); // ACTION
		expect(body).toContain('client.created'); // the raw type chip on the row
		expect(body).toContain('Feed Firm'); // the acting-firm chip
		expect(body).toContain('data-category="people"'); // the category glyph
		expect(body).toContain('AF'); // the actor's initials disc
	});

	it('maps the category glyph per event family', () => {
		const body = opened({
			props: {
				...base,
				events: [
					event({
						id: 11,
						event_type: 'role.permissions_updated',
						route: '/firm/roles/{slug}/permissions',
						method: 'PUT'
					})
				]
			}
		}).body;
		expect(body).toContain('data-category="roles"');
	});

	it('announces a proxied action by WHO did it, with the target firm as the row’s firm', () => {
		const body = opened({
			props: {
				...base,
				events: [
					event({
						id: 2,
						event_type: 'client.updated',
						method: 'PATCH',
						tenant_name: 'Graphene Operations',
						target_tenant_id: 9,
						target_tenant_uid: 'ten_target',
						target_tenant_name: 'Target Firm'
					})
				]
			}
		}).body;
		// THE CHIP ANSWERS "WHO": "Graphene support", the same two words on both surfaces. It said
		// "Acting on {firm}" before, which named the TARGET — duplicating the firm chip beside it here,
		// and reading as a failed attempt to say who did it where ops act on Graphene's own tenant.
		expect(body).toContain('Graphene support');
		expect(body).not.toContain('Acting on');
		// The target firm is the firm this ROW is about, so it is the plain firm chip's value — not the
		// operations tenant the operator signed in to, which is identical on every proxied row.
		expect(body).toContain('Target Firm');
		// No row tint: the chip carries the distinction, and an amber row reads as a warning about the
		// event rather than a statement about who came in.
		expect(body).not.toContain('bg-warning-subtle/40');
	});

	// ---- the firm branding icons (tenant_icon_url / target_tenant_icon_url) -------------------

	it("wears the acting firm's icon on the support chip, and the TARGET firm's on the row's firm chip", () => {
		const body = opened({
			props: {
				...base,
				events: [
					event({
						id: 3,
						tenant_icon_url: 'https://cdn.test/feed-firm.png',
						target_tenant_id: 9,
						target_tenant_uid: 'ten_target',
						target_tenant_name: 'Target Firm',
						target_tenant_icon_url: 'https://cdn.test/target-firm.png'
					})
				]
			}
		}).body;
		expect(body).toContain('src="https://cdn.test/feed-firm.png"');
		expect(body).toContain('src="https://cdn.test/target-firm.png"');
		// The name stays beside the icon (chip style), and hovers confirm it. The TARGET firm is the
		// row's firm now (it is what the row is about), so it is the one named in the plain chip.
		expect(body).toContain('Target Firm');
		expect(body).toContain('title="Target Firm"');
	});

	it('falls back to the name-only chip when a firm has no icon (older rows / unset branding)', () => {
		const body = opened({
			props: {
				...base,
				events: [
					event({
						id: 4,
						target_tenant_id: 9,
						target_tenant_uid: 'ten_target',
						target_tenant_name: 'Target Firm'
						// no icon urls at all — the pre-icons API shape
					})
				]
			}
		}).body;
		expect(body).toContain('Graphene support');
		expect(body).toContain('Target Firm');
		expect(body).not.toContain('<img');
		// The ACTING ops tenant is never named — a firm reading its own log is told "Graphene
		// support" did this, not which Graphene tenant. The raw-JSON block used to leak the real
		// name past that abstraction; with it gone, the whole pane honours it.
		expect(body).not.toContain('Feed Firm');
	});

	it('empty states: "nothing yet" unfiltered, "nothing matches" when filters are set', () => {
		const quiet = opened({ props: { ...base, events: [] } }).body;
		expect(quiet).toContain('No actions recorded yet');

		const filtered = opened({
			props: {
				...base,
				events: [],
				filters: { ...EMPTY_ACTION_LOG_FILTERS, eventType: ['action.nothing'] }
			}
		}).body;
		expect(filtered).toContain('No events match these filters');
	});

	it('a first-page read FAILURE reads as an error, not a definitive empty trail', () => {
		// loadError wins over both the quiet and the filtered empty states — a failed read must never
		// look like "no events" beside the page's retry banner.
		const failed = opened({
			props: { ...base, events: [], loadError: true }
		}).body;
		expect(failed).toContain('load events'); // "Couldn’t load events"
		expect(failed).toContain('The event log couldn’t be loaded.');
		expect(failed).not.toContain('No actions recorded yet');
	});

	it('offers the cursor button only when there is more stream behind the page', () => {
		expect(opened({ props: { ...base, events: [event()], hasMore: true } }).body).toContain(
			'Load more'
		);
		expect(opened({ props: { ...base, events: [event()], hasMore: false } }).body).not.toContain(
			'Load more'
		);
	});

	// ---- entities: the enriched sentence, the entity rows, the injected links ----------------

	const linkEvent = () =>
		event({
			id: 5,
			event_type: 'client.portal_user_linked',
			route: '/firm/contacts/{contact_id}/users',
			path_params: { contact_id: '96' },
			metadata: {
				correlation_id: 'corr_5',
				subject: { user_id: 'u-77', capacity: 'authorised' }
			},
			entities: [
				{ kind: 'contact', id: '96', label: 'Margaret Cole' },
				{ kind: 'user', id: 'u-77', label: 'm.cole@example.com' }
			]
		});

	it('weaves resolved entities into the sentence and renders them as labelled rows', () => {
		const e = linkEvent();
		const { body } = opened({
			props: { ...base, events: [e] }
		});
		// The enriched summary line — actor-led, capacity woven in from the subject.
		expect(body).toContain('linked m.cole@example.com to Margaret Cole as authorised');
		// The detail pane's entity rows: kind label, display name, mono id.
		expect(body).toContain('Client');
		expect(body).toContain('Margaret Cole');
		expect(body).toContain('96');
		expect(body).toContain('User');
		expect(body).toContain('u-77');
		// The subject field that is NOT an entity id renders as a change row ("Capacity authorised")…
		expect(body).toContain('Capacity');
		expect(body).toContain('authorised');
		// …while covered ids do not repeat as rows (`user_id` is already the User row) — in the
		// humanised view; the raw key lives in Details.
		expect(humanised(body)).not.toContain('user_id');
	});

	// ---- the changes-first expansion (Discord treatment) --------------------------------------

	const roleEvent = () =>
		event({
			id: 8,
			event_type: 'member.portal_role_updated',
			method: 'PATCH',
			route: '/firm/members/{user_id}/portals/{portal}',
			path_params: { user_id: 'u-77', portal: 'platform' },
			metadata: {
				correlation_id: 'corr_8',
				subject: { role_slug: 'compliance' },
				previous: { role_slug: 'adviser' }
			},
			entities: [{ kind: 'user', id: 'u-77', label: 'm.cole@example.com' }]
		});

	it('reads the role change as from → to, changes BEFORE the plumbing', () => {
		const e = roleEvent();
		const { body } = opened({
			props: { ...base, events: [e] }
		});
		// The sentence answers the product question outright.
		expect(body).toContain("changed m.cole@example.com's platform role: Adviser → Compliance");
		// The detail pane's change row: Label, old → new (slugs de-slugged).
		expect(body).toContain('Role');
		expect(body).toContain('Adviser');
		expect(body).toContain('Compliance');
		// Changes-first ordering: the change row renders ABOVE the engineer plumbing. Marked on the
		// Operation row — `Type` used to mark it too and is no longer in the inline panel at all
		// (the row's own mono chip states it), and the correlation id was dropped before that.
		expect(body.indexOf('Adviser')).toBeLessThan(body.indexOf('>Operation<'));
		expect(body.indexOf('Compliance')).toBeLessThan(body.indexOf('>Operation<'));
	});

	const permissionsEvent = (over: Partial<ActionEvent> = {}) =>
		event({
			id: 9,
			event_type: 'role.permissions_updated',
			method: 'PUT',
			route: '/firm/roles/{slug}/permissions',
			path_params: { slug: 'director' },
			metadata: {
				correlation_id: 'corr_9',
				subject: {
					permissions: ['activity:read', 'contacts:read', 'contacts:write', 'tasks:read']
				},
				previous: { permissions: ['contacts:read', 'contacts:write', 'flows:write', 'tasks:read'] }
			},
			...over
		});

	it('permissions: ONLY the delta renders — ± chips, never the full list', () => {
		const e = permissionsEvent();
		const body = opened({
			props: { ...base, events: [e] }
		}).body;
		// The summary counts…
		expect(body).toContain('updated role Director: +1 permission, −1');
		// …the chips carry the actual change…
		expect(body).toContain('+activity:read');
		expect(body).toContain('−flows:write');
		// …and the UNCHANGED slugs never render in the humanised delta (only the full Details JSON
		// carries them).
		expect(humanised(body)).not.toContain('contacts:write');
		expect(humanised(body)).not.toContain('tasks:read');
	});

	it('permissions without a previous (legacy row): a count chip, never the dump', () => {
		const e = permissionsEvent({
			metadata: {
				correlation_id: 'corr_9',
				subject: { permissions: ['activity:read', 'contacts:read', 'flows:write'] }
			}
		});
		const body = opened({
			props: { ...base, events: [e] }
		}).body;
		expect(body).toContain('3 permissions set');
		expect(humanised(body)).not.toContain('activity:read');
		expect(humanised(body)).not.toContain('flows:write');
	});

	const modelEvent = () =>
		event({
			id: 10,
			event_type: 'account.model_updated',
			method: 'PUT',
			route: '/crm/accounts/{account_id}/model',
			path_params: { account_id: '12' },
			metadata: {
				correlation_id: 'corr_10',
				subject: { model_id: 9 },
				previous: { model_id: 3 }
			},
			entities: [
				{ kind: 'account', id: '12', label: 'SIPP' },
				{ kind: 'model', id: '9', label: 'Balanced 60/40' },
				{ kind: 'model', id: '3', label: 'Cautious' }
			]
		});

	it('model change: old → new in the change row, and the old entity wears a previous pill', () => {
		const e = modelEvent();
		const body = opened({
			props: { ...base, events: [e] }
		}).body;
		expect(body).toContain("changed SIPP's model: Cautious → Balanced 60/40");
		// The change row resolves BOTH ids to labels.
		expect(body).toContain('Model');
		expect(body).toContain('Cautious');
		expect(body).toContain('Balanced 60/40');
		// The old model's entity row is marked as the before-state.
		expect(body).toContain('previous');
	});

	it('links an entity row via the injected entityHref; plain text without it', () => {
		const e = linkEvent();
		const withHref = opened({
			props: {
				...base,
				events: [e],
				entityHref: (ev: ActionEvent, ent: { kind: string; id: string }) =>
					ent.kind === 'contact' ? `/tenants/${ev.tenant_id}/book/clients/${ent.id}` : null
			}
		}).body;
		expect(withHref).toContain('href="/tenants/1/book/clients/96"');

		const e2 = linkEvent();
		const withoutHref = opened({
			props: { ...base, events: [e2] }
		}).body;
		expect(withoutHref).not.toContain('book/clients/96');
	});

	// The `facets` prop — the endpoint's whole filter vocabulary for the scope — seeds the event-type and
	// actor dropdowns at first paint, and it has to: the filters are applied by the API, so a dropdown
	// derived from the answer offers only the values that answer contains. Its pins live with the rest of
	// the filtering, in event-stream.filtering.ssr.test.ts ("offers the event-type picker from the FACETS,
	// even with no rows loaded at all") and in event-stream-filters.test.ts (the option lists themselves).
	// A stale note here once claimed facets had been removed API-side; they had not.

	it('lands with NOTHING open, before a line of JS has run', () => {
		// The server used to send the newest row already expanded, so the surface never looked empty.
		// It also meant every reader was handed one event opened for them — "this one is special"
		// where all it said was "this one is last" — and the rest of the list started a screen down.
		// Asserted on the SSR output specifically: the no-JS first paint is where that was decided.
		const table = tableOf(
			opened({
				props: { ...base, events: [event(), event({ id: 2, actor_name: 'Bo Adviser' })] }
			}).body
		);
		expect(table).not.toContain('aria-expanded="true"');
		expect(table.match(/aria-expanded="false"/g)).toHaveLength(2);
		// No detail cell either — an expanded row is a `<td colspan>` and there is no open row to
		// own one. Opening, closing and the accordion are run-time, and live in
		// event-stream.expand.dom.test.ts.
		expect(table).not.toContain('colspan=');
	});

	it('marks every row with a chevron, and lets CSS turn the one that opens', () => {
		const table = tableOf(
			opened({
				props: { ...base, events: [event(), event({ id: 2, actor_name: 'Bo Adviser' })] }
			}).body
		);
		// One per row: without it a row that expands looks exactly like one that does not.
		expect(table.match(/lucide-chevron-down/g)).toHaveLength(2);
		// The turn is CSS off the row's own `aria-expanded`, not a class the cell recomputes: a cell
		// that reads which row is open re-renders every loaded row on every click, which is the lag
		// spike opening an event used to cost. So every chevron carries the variant...
		expect(table.match(/group-aria-expanded:rotate-180/g)).toHaveLength(2);
		// ...and the `group` it resolves against is ON the row, or nothing would ever turn. (That it
		// DOES turn once a row is open needs a click — event-stream.expand.dom.test.ts.)
		expect(table.match(/<tr[^>]*class="[^"]* group [^"]*"[^>]*aria-expanded=/g)).toHaveLength(2);
	});

	it('keeps no side-pane layout at all', () => {
		// Source-shape, because a leftover `lg:w-[58%]` on a wrapper renders no text to assert on and
		// would silently halve the table again the next time someone reinstated a second column.
		const source = readFileSync(new URL('./event-stream.svelte', import.meta.url), 'utf8');
		expect(source).not.toContain('lg:w-[58%]');
		expect(source).not.toContain('lg:flex-row');
		expect(source).not.toContain('lg:border-r');
	});

	it('shows the bare id for a deleted entity (null label) instead of dropping the row', () => {
		const e = event({
			id: 6,
			event_type: 'client.updated',
			route: '/firm/contacts/{contact_id}',
			path_params: { contact_id: '424242' },
			entities: [{ kind: 'contact', id: '424242', label: null }]
		});
		const body = opened({
			props: { ...base, events: [e] }
		}).body;
		expect(body).toContain('Client');
		expect(body).toContain('424242');
		// The generic sentence survives — a null label never enriches.
		expect(body).toContain('updated a client'); // actor-led: the verb folds in lower-cased
	});

	it('sits in the glass listing card', () => {
		expect(render(EventStream, { props: { ...base, events: [event()] } }).body).toContain(
			'glass-panel'
		);
	});
});
