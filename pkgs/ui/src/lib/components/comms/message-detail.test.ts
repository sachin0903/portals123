import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
	SAME_AS_PREVIEW,
	formatRawRequest,
	latestEntry,
	requestLine,
	timelineFor
} from './message-detail.js';
import { humanise, recipientLabel, statusMeta, templateLabel } from './describe.js';
import type { CommsMessage } from './describe.js';

const BASE: CommsMessage = {
	id: 1,
	uid: 'abc123abc123',
	status: 'sent',
	delayed_at: null,
	subject: 'Your suitability report',
	to_address: 'ada@example.test',
	to_domain: 'example.test',
	recipient_kind: 'contact',
	recipient_contact_id: 7,
	recipient_name: 'Ada Okafor',
	template_code: 'document_sent',
	event_type: 'document.sent',
	rule_uid: 'nrule_x',
	category: 'documents',
	source: 'worker',
	error: null,
	provider_message_id: 're_1',
	created_at: '2026-09-01T14:02:00.000Z',
	sent_at: '2026-09-01T14:02:00.000Z',
	delivered_at: null,
	bounced_at: null,
	complained_at: null
};

const msg = (over: Partial<CommsMessage>): CommsMessage => ({ ...BASE, ...over });

describe('timelineFor', () => {
	it('reports only the stamps that exist, oldest first', () => {
		const entries = timelineFor(
			msg({
				status: 'delivered',
				delayed_at: '2026-09-01T14:02:10.000Z',
				delivered_at: '2026-09-01T14:02:30.000Z'
			})
		);
		expect(entries.map((e) => e.key)).toEqual(['sent', 'delayed', 'delivered']);
		expect(entries.map((e) => e.label)).toEqual(['Sent', 'Delayed', 'Delivered']);
	});

	it('orders by instant, not by the order the stamps are declared', () => {
		// Out-of-order callbacks are ordinary, so a bounce recorded before the delivery callback was
		// processed can carry the EARLIER instant of the two.
		const entries = timelineFor(
			msg({
				status: 'bounced',
				sent_at: '2026-09-01T10:00:00.000Z',
				bounced_at: '2026-09-01T11:00:00.000Z',
				delivered_at: '2026-09-01T12:00:00.000Z'
			})
		);
		expect(entries.map((e) => e.key)).toEqual(['sent', 'bounced', 'delivered']);
	});

	it('breaks a tie by rank so a same-second delivery still reads after its send', () => {
		// A delivery inside the same second as the send is the COMMON case, not an edge one, and
		// showing "Delivered" above "Sent" reads as a clock error.
		const at = '2026-09-01T14:02:00.000Z';
		const entries = timelineFor(msg({ status: 'delivered', sent_at: at, delivered_at: at }));
		expect(entries.map((e) => e.key)).toEqual(['sent', 'delivered']);
	});

	it('counts a repeat, and stays silent about a single occurrence', () => {
		const entries = timelineFor(
			msg({
				status: 'delivered',
				delivered_at: '2026-09-01T14:31:00.000Z',
				events: [
					{ event_type: 'email.sent', occurred_at: null, created_at: '' },
					{ event_type: 'email.delivered', occurred_at: null, created_at: '' },
					{ event_type: 'email.delivered', occurred_at: null, created_at: '' },
					{ event_type: 'email.delivered', occurred_at: null, created_at: '' }
				]
			})
		);
		expect(entries.find((e) => e.key === 'delivered')?.times).toBe(3);
		expect(entries.find((e) => e.key === 'sent')?.times).toBeUndefined();
	});

	it('does not report a count when the events were not fetched', () => {
		// The LISTING has no events. A count of 0 must not render as "never happened", and a
		// count of 1 would be noise on every row.
		const entries = timelineFor(msg({ status: 'delivered', delivered_at: BASE.created_at }));
		expect(entries.every((e) => e.times === undefined)).toBe(true);
	});

	it('keeps a delivery that was later bounced — both are true and both are shown', () => {
		const entries = timelineFor(
			msg({
				status: 'bounced',
				delivered_at: '2026-09-01T14:02:30.000Z',
				bounced_at: '2026-09-02T09:00:00.000Z'
			})
		);
		expect(entries.map((e) => e.key)).toEqual(['sent', 'delivered', 'bounced']);
		expect(entries.at(-1)?.tone).toBe('danger');
	});

	describe('a message that never left', () => {
		it.each(['suppressed', 'unconfigured', 'failed'] as const)(
			'explains %s instead of showing an empty timeline',
			(status) => {
				const entries = timelineFor(
					msg({
						status,
						sent_at: null,
						error: "APP_STAGE='dev' is not production and EMAIL_ALLOWLIST is empty"
					})
				);
				expect(entries).toHaveLength(1);
				expect(entries[0].key).toBe(status);
				// The REASON is the content of that entry — a bare "Not sent" with no stamps
				// reads as missing data rather than as a deliberate outcome.
				expect(entries[0].detail).toContain('EMAIL_ALLOWLIST');
				expect(entries[0].at).toBe(BASE.created_at);
			}
		);

		it('ignores any stray stamps, because nothing was posted for them to describe', () => {
			const entries = timelineFor(
				msg({ status: 'suppressed', sent_at: BASE.created_at, delivered_at: BASE.created_at })
			);
			expect(entries).toHaveLength(1);
			expect(entries[0].key).toBe('suppressed');
		});
	});
});

describe('latestEntry', () => {
	it('gives the row the most recent fact, not the first', () => {
		// A reader scanning the feed needs "Bounced", not "Sent" — the row has one line to spend.
		const entry = latestEntry(
			msg({
				status: 'bounced',
				delivered_at: '2026-09-01T14:02:30.000Z',
				bounced_at: '2026-09-02T09:00:00.000Z'
			})
		);
		expect(entry?.key).toBe('bounced');
	});
});

describe('the status vocabulary', () => {
	it('does not call a handoff to the provider a delivery', () => {
		// The overclaim the whole ledger exists to stop: `sent` means Resend accepted it.
		expect(statusMeta('sent').tone).toBe('info');
		expect(statusMeta('sent').label).toBe('Sent');
		expect(statusMeta('delivered').tone).toBe('success');
		expect(statusMeta('delivered').label).toBe('Delivered');
		expect(statusMeta('delayed').label).toBe('Delayed');
	});

	it('treats a refusal as bad news and a deliberate non-send as neutral', () => {
		expect(statusMeta('bounced').tone).toBe('danger');
		expect(statusMeta('complained').tone).toBe('danger');
		expect(statusMeta('failed').tone).toBe('danger');
		// A deployment that withholds mail still has a row; calling it "Suppressed" is our jargon,
		// not a delivery state a firm would scan for.
		expect(statusMeta('suppressed').tone).toBe('neutral');
		expect(statusMeta('suppressed').label).toBe('Not sent');
		expect(statusMeta('unconfigured').tone).toBe('neutral');
		expect(statusMeta('unconfigured').label).toBe('Not sent');
	});
});

describe('labels', () => {
	it('prefers the client name and falls back to the address', () => {
		expect(recipientLabel(BASE)).toBe('Ada Okafor');
		expect(recipientLabel(msg({ recipient_name: null }))).toBe('ada@example.test');
		// A name of whitespace is not a name.
		expect(recipientLabel(msg({ recipient_name: '   ' }))).toBe('ada@example.test');
	});

	it('names a template it knows, and reads an unknown one as words', () => {
		// Both branches are user-visible in the SAME place: `templateLabel` supplies the Template
		// filter's option labels as well as the detail pane's. A firm adding a template code the
		// portal has no name for must get "Client welcome" in that dropdown, not `client_welcome` —
		// a filter listing raw keys is one a reader cannot use.
		expect(templateLabel('password_reset')).toBe('Password reset');
		expect(templateLabel('client_welcome')).toBe('Client welcome');
		expect(templateLabel(null)).toBe('No template');
		// `humanise` is the same fallback the Category filter runs on directly.
		expect(humanise('document.sent')).toBe('Document sent');
		expect(humanise('')).toBe('');
	});
});

describe('formatRawRequest', () => {
	it('pretty-prints the stored request when present', () => {
		const text = formatRawRequest(
			msg({
				html: '<p>preview</p>',
				request: {
					method: 'POST',
					body: { to: ['ada@example.test'], html: '<p>wire</p>' }
				}
			})
		);
		expect(text).toContain('"method": "POST"');
		expect(text).toContain('<p>wire</p>');
		expect(text).not.toContain('preview');
	});

	it('reconstructs the envelope from the preview when the request was not stored', () => {
		const text = formatRawRequest(msg({ html: '<p>preview</p>' }));
		expect(text).toContain('"to": [');
		expect(text).toContain('ada@example.test');
		expect(text).toContain('<p>preview</p>');
		expect(text).not.toMatch(/^<p>preview<\/p>$/);
	});
});

describe('the body is never rendered into the portal document', () => {
	// A source scan, because this is a security property of the MARKUP and no runtime assertion
	// reaches it: a component that swapped the frame for `{@html}` would render identically in a
	// snapshot and still hand an email's script the adviser's session.
	const source = readFileSync(new URL('./message-detail.svelte', import.meta.url), 'utf8');
	/**
	 * The MARKUP only — everything after the component's script block.
	 *
	 * Scoped, because the file's own header tells the next reader not to swap the frame for
	 * `{@html}`, and a file-wide scan trips over the comment explaining the rule. That is the
	 * documented false positive `switch-columns.test.ts` hit, and the answer there was the same:
	 * scope the scan to where the thing would actually do harm.
	 */
	const markup = source
		.slice(source.lastIndexOf('</script>') + '</script>'.length)
		// COMMENTS STRIPPED, the way `rules-listing.test.ts` does it. The component's own markup
		// tells the next reader not to swap the frame for a raw HTML injection, and a scan that
		// cannot tell a warning from the thing it warns about fails on the documentation. This is
		// the fourth time that has happened in this area; strip, do not reword.
		.replace(/<!--[\s\S]*?-->/g, '');

	it('uses the shared sandboxed frame rather than its own iframe', () => {
		expect(source).toContain('EmailPreviewFrame');
		// Not a hand-rolled one: the sandbox attribute belongs in exactly one component, whose own
		// test pins it. A copy here is what eventually grows an `allow-scripts`.
		expect(markup).not.toMatch(/<iframe/i);
	});

	it('never injects the body as raw HTML', () => {
		expect(markup).not.toContain('@html');
	});

	it('does not dump the preview HTML as the Raw tab', () => {
		// Scoped to the Raw pane: `value="raw"` on the TRIGGER sits above the preview, which
		// legitimately binds `{message.html}` into the iframe. A file-wide / trigger-forward
		// scan would fail on that, the way `switch-columns.test.ts` failed on a comment.
		const rawPane = markup.match(/<Tabs\.Content value="raw"[\s\S]*?<\/Tabs\.Content>/)?.[0];
		expect(rawPane).toBeDefined();
		// `rawLines` is `raw` run through the shared JSON tokeniser — the pane renders per-segment
		// spans now rather than the string, so the assertion is about WHICH source it reads.
		expect(rawPane).toContain('rawLines');
		expect(rawPane).not.toContain('{message.html}');
		expect(rawPane).not.toContain('htmlLines');
	});
});

describe('the body is stored once', () => {
	/**
	 * `comms.messages.html` already holds the body, and for an email with no inlined images the
	 * wire copy is byte-identical — so the ledger elides it and the pane splices it back. The
	 * alternative was carrying the largest column on the table twice with no pruning job.
	 */
	const base = {
		id: 1,
		uid: 'abc123abc123',
		status: 'sent' as const,
		subject: 'Hello',
		to_address: 'ada@example.test',
		to_domain: 'example.test',
		recipient_kind: 'contact' as const,
		recipient_contact_id: 7,
		recipient_name: null,
		template_code: 'invite',
		event_type: null,
		category: null,
		source: 'app' as const,
		error: null,
		created_at: '2026-09-01T14:02:00.000Z',
		sent_at: '2026-09-01T14:02:00.000Z',
		delayed_at: null,
		delivered_at: null,
		bounced_at: null,
		complained_at: null
	};

	it('shows the stored body where the wire copy was elided', () => {
		const raw = formatRawRequest({
			...base,
			html: '<p>the body</p>',
			request: { method: 'POST', body: { to: ['ada@example.test'], html: SAME_AS_PREVIEW } }
		} as never);
		expect(raw).toContain('<p>the body</p>');
		expect(raw).not.toContain(SAME_AS_PREVIEW);
	});

	it('leaves a wire body that genuinely differs alone', () => {
		// Inlining rewrites image URLs into `cid:` references, and that markup is the only place
		// "did the logo go as an attachment" is answerable from.
		const raw = formatRawRequest({
			...base,
			html: '<img src="https://cdn.example.test/logo.png">',
			request: { method: 'POST', body: { html: '<img src="cid:logo">' } }
		} as never);
		expect(raw).toContain('cid:logo');
		expect(raw).not.toContain('cdn.example.test');
	});
});

describe('requestLine', () => {
	const withRequest = (request: unknown) =>
		requestLine(msg({ request: request as Record<string, unknown> | null }));

	it('reads as the log line Resend shows: method and PATH, no host', () => {
		// The host is a constant of the deployment and this field has a quarter of a row to fit in.
		expect(withRequest({ method: 'POST', url: 'https://api.resend.com/emails' })).toBe(
			'POST /emails'
		);
	});

	it('is null when there is no request, so the pane can say "Not recorded"', () => {
		// A row written before `0118_comms_request`. An empty field reads as a rendering fault.
		expect(withRequest(null)).toBeNull();
		expect(withRequest(undefined)).toBeNull();
		expect(withRequest({})).toBeNull();
	});

	it('keeps a url it cannot parse rather than dropping the field', () => {
		// Whatever was recorded is more use than nothing, and this is a display string — not
		// something anything navigates to.
		expect(withRequest({ method: 'POST', url: '/emails' })).toBe('POST /emails');
		expect(withRequest({ method: 'POST', url: 'not a url' })).toBe('POST not a url');
	});

	it('survives a half-recorded request', () => {
		expect(withRequest({ url: 'https://api.resend.com/emails' })).toBe('/emails');
		expect(withRequest({ method: 'POST' })).toBe('POST');
		// Non-string values are ignored rather than stringified into "[object Object] undefined".
		expect(withRequest({ method: 7, url: { a: 1 } })).toBeNull();
	});
});
