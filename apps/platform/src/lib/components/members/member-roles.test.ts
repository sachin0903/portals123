/**
 * The role picker's selection must be seeded by the ROW, not by a page-level map.
 *
 * Team and Profiles each held a `Record<userId, string[]>` in `$state`, seeded it from an
 * `$effect`, and bound a MultiSelect to `map[member.user_id]`. `bind:` on an entry that is still
 * `undefined` writes the child's own `[]` fallback back into the map during its first render —
 * before any effect runs — so the seeding guard found the key already present and left it empty.
 * Every row on both pages read "Choose roles…" over a member who did hold roles, while the API had
 * been returning them correctly all along, which is a failure no amount of API testing could see.
 *
 * A static scan is weaker than mounting the pages, and is what is available: the platform app's
 * vitest runs in node with no jsdom project (only `pkgs/ui` can mount a component). It pins the
 * shape of the fix rather than its behaviour, so it is stated as such.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const PAGES = {
	Team: 'src/routes/team/+page.svelte',
	Profiles: 'src/routes/profiles/+page.svelte'
} as const;

/** `bind:value={anything[…]}` — a binding whose target is an index into a collection. */
const BIND_TO_INDEX = /bind:value=\{[A-Za-z0-9_.$]+\[/;

describe('the row role picker', () => {
	for (const [page, path] of Object.entries(PAGES)) {
		const source = readFileSync(path, 'utf8');

		it(`${page} seeds each row's roles through MemberRolesForm`, () => {
			expect(source).toContain('MemberRolesForm');
			expect(source).toContain('initial={membership?.role_slugs ?? []}');
		});

		it(`${page} never binds a picker to a map entry`, () => {
			expect(BIND_TO_INDEX.test(source)).toBe(false);
		});
	}

	it('seeds from the prop synchronously, with no effect in the way', () => {
		const source = readFileSync('src/lib/components/members/member-roles-form.svelte', 'utf8');
		expect(source).toContain('$state(untrack(() => [...initial]))');
		// `$effect(` the CALL, not the word — the comment above the state explains the bug by name.
		expect(source).not.toContain('$effect(');
	});
});
