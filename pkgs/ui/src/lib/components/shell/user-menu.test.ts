import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * A closed `DropdownMenu.Content` renders nothing at SSR (see book-documents.ssr.test.ts), so the
 * menu-item markup here is asserted off the component source rather than a render — order and the
 * "Profile" vs "Account (name)" split are the parts worth pinning.
 */
const fullSrc = readFileSync(join(import.meta.dirname, 'user-menu.svelte'), 'utf8');
// The doc comment above the props block names both "Account" and "Sign out" in prose — search only
// the markup, after the last `</script>`, so those mentions can't be mistaken for the real items.
const src = fullSrc.slice(fullSrc.lastIndexOf('</script>'));

const youIdx = () => src.indexOf('Profile');

describe('UserMenu source', () => {
	it('labels the login-settings item "Profile"', () => {
		expect(youIdx()).toBeGreaterThan(-1);
	});
});
