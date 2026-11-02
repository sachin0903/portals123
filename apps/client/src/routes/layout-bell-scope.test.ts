/**
 * Every endpoint the client portal hands its notification bell is narrowed to the ACTIVE contact.
 *
 * A login linked to more than one client record picks one active client in the nav switcher. The
 * bell's badge, its list and its live channel are three separate URLs, and each one that forgets
 * `?contact_id=` falls back to RLS alone — which is every linked contact. That is how the dropdown
 * came to merge two clients' notifications while the badge next to it counted one: `countEndpoint`
 * was narrowed and `feedEndpoint` was not, and nothing could see the difference. A prop is a string
 * here, so no type can hold this rule; the source is scanned instead — the idiom
 * `route-addressing.test.ts` and `switch-columns.test.ts` use for the same reason.
 *
 * It is a scan of the signed-in shell, not of the shared component: `@graphene/ui`'s bell is
 * portal-agnostic and correct either way. Which client it is asked about is this portal's
 * decision alone. The shell lives in `portal-chrome.svelte` so the photo page never loads it.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const LAYOUT = new URL('./portal-chrome.svelte', import.meta.url);
const source = readFileSync(LAYOUT, 'utf8');

/** The bell's props that name a URL. Add one here when the component gains one. */
const URL_PROPS = ['countEndpoint', 'feedEndpoint', 'realtimeUrl'] as const;

/** The `<NotificationBell … />` element as written, so a URL elsewhere in the layout cannot pass. */
const bell = source.slice(
	source.indexOf('<NotificationBell'),
	source.indexOf('/>', source.indexOf('<NotificationBell'))
);

describe('the client bell is scoped to the active contact', () => {
	it('mounts the bell at all', () => {
		expect(bell).toContain('<NotificationBell');
	});

	for (const prop of URL_PROPS) {
		it(`${prop} carries contact_id`, () => {
			const at = bell.indexOf(`${prop}=`);
			expect(at, `${prop} is not passed to the bell`).toBeGreaterThan(-1);
			// The value runs to the next prop on its own line, which is where the `\n\t\t` before a
			// word-and-equals begins; the ternary's own newlines are indented further.
			const value = bell.slice(at, at + 220);
			expect(value, `${prop} must narrow to data.activeContactId`).toContain(
				'contact_id=${data.activeContactId}'
			);
		});
	}

	it('narrows the bulk writes too', () => {
		// Marking or dismissing EVERYTHING is a write over whatever the filter allows, so an
		// unnarrowed one clears the other linked client's notifications as collateral.
		expect(bell).toContain('markNotification(undefined, data.activeContactId)');
		expect(bell).toContain('dismissAllNotifications(data.activeContactId)');
	});

	it('does not mount while the client is onboarding', () => {
		const beforeBell = source.slice(
			Math.max(0, source.indexOf('<NotificationBell') - 80),
			source.indexOf('<NotificationBell')
		);
		expect(beforeBell).toContain('{#if !data.onboarding}');
	});
});
