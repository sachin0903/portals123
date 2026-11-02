/**
 * The directory's empty states.
 *
 * Two behavioural fixes landed here with no coverage at all, and both are the kind that fail
 * silently: the table draws `visible`, so testing `rows` meant a local-mode search matching nobody
 * rendered an empty table body under a populated count and NO empty state; and a search miss wore
 * the caller's "No members yet", telling a firm with two hundred people it had none.
 *
 * SSR reaches both because the search is seeded from the `query` prop rather than from typing.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { createRawSnippet } from 'svelte';
import MemberDirectory from './member-directory.svelte';
import type { MemberDirectoryRow } from './member-directory.svelte';

// `roles` is required, and none of these cases is about it — a raw snippet keeps the fixtures
// honest without a second component file.
const roles = createRawSnippet(() => ({ render: () => '<span></span>' }));

const row = (over: Partial<MemberDirectoryRow> = {}): MemberDirectoryRow => ({
	id: 'usr_1',
	name: 'Priya Shah',
	email: 'priya@firm.test',
	status: 'active',
	...over
});

describe('MemberDirectory empty states (SSR)', () => {
	it('shows the caller’s own words when nobody has been invited', () => {
		const { body } = render(MemberDirectory, {
			props: {
				title: 'Team members',
				rows: [],
				roles,
				emptyTitle: 'No team members yet',
				emptyDescription: 'People appear here once they’re invited.'
			}
		});
		expect(body).toContain('No team members yet');
		expect(body).toContain('People appear here once they’re invited.');
		expect(body).not.toContain('Nobody matches that search');
	});

	it('LOCAL MODE: a search matching nobody is its own state, not the caller’s', () => {
		// Without `onsearch` the component filters in-process. It used to test `rows.length`, which
		// is the unfiltered set — so this rendered an empty table and no message at all.
		const { body } = render(MemberDirectory, {
			props: {
				title: 'Team members',
				rows: [row()],
				roles,
				query: 'zzzz',
				emptyTitle: 'No team members yet'
			}
		});
		expect(body).toContain('Nobody matches that search');
		expect(body).toContain('zzzz');
		// The caller's "nothing yet" words must NOT be shown for a search miss — that is the whole
		// defect: telling a firm with two hundred people that it has none.
		expect(body).not.toContain('No team members yet');
		expect(body).toContain('Clear search');
	});

	it('SERVER MODE: the same, when the server is the one that found nobody', () => {
		const { body } = render(MemberDirectory, {
			props: {
				title: 'Profiles',
				rows: [],
				roles,
				query: 'halloway',
				onsearch: () => {},
				emptyTitle: 'No profiles yet'
			}
		});
		expect(body).toContain('Nobody matches that search');
		expect(body).not.toContain('No profiles yet');
	});
});
