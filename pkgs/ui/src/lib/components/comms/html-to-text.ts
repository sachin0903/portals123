/**
 * HTML → readable plain text for the Activity detail's Plain text tab.
 *
 * Display only: the ledger stores the HTML that was sent, not a second body. Stripping is string
 * work, never a DOM parse — this module is imported from the detail page, which SSRs, and a
 * `DOMParser`/`{@html}` path would be the sandbox hole `message-detail.test.ts` exists to catch.
 *
 * Newlines are inserted at block boundaries so a table-based email does not collapse into one
 * line; tags and `<script>`/`<style>` are dropped, not executed.
 */
export function htmlToPlainText(html: string): string {
	if (!html) return '';
	let text = html
		.replace(/<script\b[\s\S]*?<\/script>/gi, '')
		.replace(/<style\b[\s\S]*?<\/style>/gi, '')
		.replace(/<br\s*\/?>/gi, '\n')
		.replace(/<\/(p|div|h[1-6]|tr|li|blockquote|table|hr)>/gi, '\n')
		.replace(/<\/td>/gi, ' ')
		.replace(/<[^>]+>/g, '')
		.replace(/&nbsp;/gi, ' ')
		.replace(/&amp;/g, '&')
		.replace(/&lt;/g, '<')
		.replace(/&gt;/g, '>')
		.replace(/&quot;/g, '"')
		.replace(/&#39;|&apos;/g, "'");
	text = text
		.replace(/\r\n/g, '\n')
		.replace(/[ \t]+\n/g, '\n')
		.replace(/\n{3,}/g, '\n\n');
	return text.trim();
}
