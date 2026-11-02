import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import ArticleEditor from './article-editor.svelte';

describe('ArticleEditor (SSR)', () => {
	it('renders a host for the canvas without loading GrapesJS', () => {
		const { body } = render(ArticleEditor, { props: { html: '<p>Hi</p>' } });
		expect(body).toContain('article-gjs');
		expect(body).toContain('Article canvas');
		expect(body).toContain('Smart import Word');
		expect(body).toContain('Heading');
		expect(body).toContain('Callout');
	});
});
