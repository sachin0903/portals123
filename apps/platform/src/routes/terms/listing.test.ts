import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');

describe('terms listing', () => {
	it('follows the redirect the create action returns', () => {
		// A use:enhance callback replaces SvelteKit's default result handling. Only closing the
		// sheet swallowed the redirect: the term was created and the editor never opened.
		const callback = source.slice(source.indexOf('use:enhance'), source.indexOf('</form>'));
		expect(callback).toContain('goto(result.location');
		expect(callback).toContain('applyAction(result)');
	});
});
