export type WordImportResult = {
	html: string;
	// No `css`: an import is structure, and the look is the editor's own stylesheet (see `create.ts`).
	warnings: string[];
};

const STYLE_MAP = [
	"p[style-name='Title'] => h1:fresh",
	"p[style-name='Subtitle'] => p.article-lede:fresh",
	"p[style-name='Heading 1'] => h2:fresh",
	"p[style-name='Heading 2'] => h3:fresh",
	"p[style-name='Heading 3'] => h3:fresh",
	"p[style-name='Intense Quote'] => blockquote:fresh",
	"p[style-name='Quote'] => blockquote:fresh"
];

function fileFromBase64(content: string, contentType: string, index: number): File {
	const binary = atob(content);
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
	const extension =
		contentType === 'image/png'
			? 'png'
			: contentType === 'image/gif'
				? 'gif'
				: contentType === 'image/webp'
					? 'webp'
					: 'jpg';
	return new File([bytes], `word-image-${index}.${extension}`, { type: contentType });
}

/**
 * Turn Mammoth's deliberately plain semantic HTML into the article vocabulary. Word's visual
 * formatting is discarded: headings, lists, tables, emphasis and quotes survive, then the portal's
 * editorial stylesheet supplies the look. This is why an imported document does not inherit a
 * patchwork of Calibri sizes, indents and page margins.
 */
export function normaliseWordHtml(raw: string): string {
	const parsed = new DOMParser().parseFromString(`<div id="word-import">${raw}</div>`, 'text/html');
	const root = parsed.querySelector<HTMLElement>('#word-import');
	if (!root) return `<div class="article-shell">${raw}</div>`;

	for (const empty of root.querySelectorAll('p, h1, h2, h3, blockquote')) {
		if (!empty.textContent?.trim() && !empty.querySelector('img')) empty.remove();
	}
	for (const element of root.querySelectorAll<HTMLElement>('*')) {
		element.removeAttribute('style');
		if (!element.classList.contains('article-lede')) element.removeAttribute('class');
	}
	for (const image of root.querySelectorAll('img')) {
		image.setAttribute('loading', 'lazy');
		if (!image.hasAttribute('alt')) image.setAttribute('alt', '');
	}

	let title = root.querySelector('h1');
	if (!title) {
		const first = root.firstElementChild;
		const text = first?.textContent?.trim() ?? '';
		if (first?.tagName === 'P' && text.length > 0 && text.length <= 120) {
			const heading = parsed.createElement('h1');
			heading.innerHTML = first.innerHTML;
			first.replaceWith(heading);
			title = heading;
		}
	}
	if (title && !title.previousElementSibling) {
		const kicker = parsed.createElement('p');
		kicker.className = 'article-kicker';
		kicker.textContent = 'Insight';
		title.before(kicker);
	}
	if (title) {
		let lead = title.nextElementSibling;
		while (lead && lead.tagName === 'IMG') lead = lead.nextElementSibling;
		if (lead?.tagName === 'P' && !lead.classList.contains('article-lede')) {
			lead.classList.add('article-lede');
		}
	}

	return `<div class="article-shell">${root.innerHTML}</div>`;
}

export async function smartImportWord(
	file: File,
	uploadImage?: (file: File) => Promise<string>
): Promise<WordImportResult> {
	if (!file.name.toLowerCase().endsWith('.docx')) {
		throw new Error('Choose a Word .docx file.');
	}

	const mammoth = await import('mammoth');
	let imageIndex = 0;
	const result = await mammoth.convertToHtml(
		{ arrayBuffer: await file.arrayBuffer() },
		{
			styleMap: STYLE_MAP,
			includeDefaultStyleMap: true,
			convertImage: mammoth.images.imgElement(async (image) => {
				imageIndex += 1;
				const base64 = await image.read('base64');
				if (!uploadImage) {
					return { src: `data:${image.contentType};base64,${base64}` };
				}
				const uploaded = await uploadImage(fileFromBase64(base64, image.contentType, imageIndex));
				return { src: uploaded };
			})
		}
	);

	return {
		html: normaliseWordHtml(result.value),
		warnings: result.messages
			.filter((message) => message.type === 'warning')
			.map((message) => message.message)
	};
}
