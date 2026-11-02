import {
	MM_TO_PX,
	PAGE_DIMS,
	type BlockType,
	type TemplateBlock,
	type TemplateContent,
	type TemplatePage
} from './types.js';

/** Derive a reference code from a free-text name (lower_snake_case). */
export function slugifyCode(name: string): string {
	return name
		.toLowerCase()
		.trim()
		.replace(/[^a-z0-9]+/g, '_')
		.replace(/^_+|_+$/g, '');
}

/** Split text into plain + merge-token ({{ … }}) segments for styled rendering. */
export function parseTokens(text: string): { value: string; token: boolean }[] {
	const out: { value: string; token: boolean }[] = [];
	const re = /\{\{.*?\}\}/g;
	let last = 0;
	let m: RegExpExecArray | null;
	while ((m = re.exec(text)) !== null) {
		if (m.index > last) out.push({ value: text.slice(last, m.index), token: false });
		out.push({ value: m[0], token: true });
		last = m.index + m[0].length;
	}
	if (last < text.length) out.push({ value: text.slice(last), token: false });
	return out;
}

/** Base (100% zoom) sheet dimensions in px for a content's size + orientation. */
export function pageSizePx(content: Pick<TemplateContent, 'size' | 'orientation'>): {
	w: number;
	h: number;
} {
	const d = PAGE_DIMS[content.size];
	const portrait = content.orientation === 'portrait';
	return { w: (portrait ? d.w : d.h) * MM_TO_PX, h: (portrait ? d.h : d.w) * MM_TO_PX };
}

/** A fresh, empty single-page template (used when first opening the editor). */
export function blankContent(): TemplateContent {
	return {
		size: 'A4',
		orientation: 'portrait',
		margins: { top: 19, right: 19, bottom: 19, left: 19 },
		background: 'none',
		border: 'none',
		pages: [{ uid: 'page-1', blocks: [] }]
	};
}

let blockSeq = 0;
/** Default block for a given palette type/variant, with a unique uid (for editor inserts). */
export function makeBlock(type: BlockType, variant?: string): TemplateBlock {
	blockSeq += 1;
	const uid = `blk-${blockSeq}`;
	switch (type) {
		case 'heading':
			return { uid, type, text: 'Heading', level: 2, align: 'left' };
		case 'paragraph':
			return {
				uid,
				type,
				text: 'New paragraph text. Use {{ merge_field }} tokens for data.',
				align: 'left'
			};
		case 'image':
			return { uid, type, label: 'Logo', align: 'left' };
		case 'labelValue':
			return { uid, type, label: 'Label', text: '{{ value }}' };
		case 'bulletList':
			return { uid, type, text: 'First item\nSecond item\nThird item' };
		case 'numberedList':
			return { uid, type, text: 'First step\nSecond step\nThird step' };
		case 'divider':
			return { uid, type };
		case 'noteBox':
			return { uid, type, text: 'Note: important information for the reader.' };
		case 'highlightBox':
			return { uid, type, text: 'Key figure or highlighted message.' };
		case 'spacer':
			return { uid, type, height: 24 };
		case 'columns':
			return { uid, type, variant: variant ?? 'two' };
		case 'chart':
			return { uid, type, variant: variant ?? 'bar', label: 'Chart title' };
		case 'table':
			return { uid, type, variant: variant ?? 'data', label: 'Table' };
		case 'pageBreak':
			return { uid, type };
		case 'conditional':
			return { uid, type, text: 'client.has_isa' };
		case 'repeat':
			return { uid, type, text: 'holdings' };
	}
}

let pageSeq = 0;
export function makePage(): TemplatePage {
	pageSeq += 1;
	return { uid: `page-new-${pageSeq}`, blocks: [] };
}
