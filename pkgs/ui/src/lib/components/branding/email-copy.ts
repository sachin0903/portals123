// The PNG an email carries of an SVG logo or icon, drawn here in the browser at upload.
//
// A mail client draws PNG, JPEG and GIF inline and nothing else: Outlook shows an SVG logo as a FILE
// ATTACHMENT above the message, and the picture in the body is broken. So the API stores a PNG
// twin beside a logo or icon in another type (`apps/api/app/branding_uploads.py`). It can draw an
// SVG itself (CairoSVG), but a browser draws SVG more faithfully, and a dev box without the Cairo
// library cannot draw one at all — so for an SVG the studio draws the PNG and sends it with the
// upload as `email_copy`, which the API prefers. It checks it is a real PNG and re-fits it; a null
// here means the API draws its own.
//
// Safe to draw: an SVG loaded as an IMAGE runs no script and loads no other file, whatever it holds.

/** The upload kinds an email reads (`EMAIL_COPY_KINDS` in `apps/api/app/branding_uploads.py`). */
export const EMAIL_COPY_KINDS: ReadonlySet<string> = new Set(['logo', 'icon']);

/** The box the copy is drawn to fit — the API's `imaging.EMAIL_COPY_BOX`, which it re-fits to. An
 * SVG has no pixels to run out of, so it is drawn at the box size, not at its nominal size. */
export const EMAIL_COPY_BOX = { width: 640, height: 256 } as const;

export function isSvg(file: File): boolean {
	return file.type === 'image/svg+xml' || /\.svg$/i.test(file.name);
}

/** A plain length (`120`, `120px`) as a number; null for `100%`, `10em` or nothing at all, which
 * say nothing about the picture's shape. */
function plainLength(value: string | null): number | null {
	const m = /^\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*$/.exec(value ?? '');
	const n = m?.[1] ? parseFloat(m[1]) : NaN;
	return Number.isFinite(n) && n > 0 ? n : null;
}

/** The SVG's own width and height: its `width`/`height` when both are plain lengths, else its
 * `viewBox` (an Illustrator export carries only that). Null when neither says. */
export function svgSize(root: Element): { width: number; height: number } | null {
	const width = plainLength(root.getAttribute('width'));
	const height = plainLength(root.getAttribute('height'));
	if (width && height) return { width, height };
	const box = (root.getAttribute('viewBox') ?? '')
		.trim()
		.split(/[\s,]+/)
		.map(Number);
	const [, , vw, vh] = box;
	if (box.length !== 4 || !vw || !vh || !(vw > 0) || !(vh > 0)) return null;
	if (width) return { width, height: (width * vh) / vw };
	if (height) return { width: (height * vw) / vh, height };
	return { width: vw, height: vh };
}

/** `size` scaled to fit `EMAIL_COPY_BOX`, aspect kept, whole pixels. */
export function fitEmailBox(size: { width: number; height: number }): {
	width: number;
	height: number;
} {
	const scale = Math.min(EMAIL_COPY_BOX.width / size.width, EMAIL_COPY_BOX.height / size.height);
	return {
		width: Math.max(1, Math.round(size.width * scale)),
		height: Math.max(1, Math.round(size.height * scale))
	};
}

/** The SVG's markup with an explicit pixel `width` and `height`, which Firefox needs before it will
 * draw an SVG image at all; null when the file is not an SVG with a size. */
export function sizedSvg(markup: string): { markup: string; width: number; height: number } | null {
	const doc = new DOMParser().parseFromString(markup, 'image/svg+xml');
	const root = doc.documentElement;
	if (!root || root.localName !== 'svg' || doc.getElementsByTagName('parsererror').length > 0) {
		return null;
	}
	const size = svgSize(root);
	if (!size) return null;
	const { width, height } = fitEmailBox(size);
	if (!root.getAttribute('viewBox')) {
		root.setAttribute('viewBox', `0 0 ${size.width} ${size.height}`);
	}
	root.setAttribute('width', String(width));
	root.setAttribute('height', String(height));
	return { markup: new XMLSerializer().serializeToString(root), width, height };
}

/** The PNG copy of an SVG `file`, drawn to fit `EMAIL_COPY_BOX`; null when it cannot be drawn. Never
 * throws: a copy that cannot be made must not stop the upload. */
export async function svgEmailCopy(file: File): Promise<Blob | null> {
	try {
		const sized = sizedSvg(await file.text());
		if (!sized) return null;
		const url = URL.createObjectURL(new Blob([sized.markup], { type: 'image/svg+xml' }));
		try {
			const img = new Image(sized.width, sized.height);
			img.src = url;
			await img.decode();
			const canvas = document.createElement('canvas');
			canvas.width = sized.width;
			canvas.height = sized.height;
			const ctx = canvas.getContext('2d');
			if (!ctx) return null;
			ctx.drawImage(img, 0, 0, sized.width, sized.height);
			return await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'));
		} finally {
			URL.revokeObjectURL(url);
		}
	} catch {
		return null;
	}
}

/** The email copy to send with an upload of `kind`, or null when it needs none from the browser —
 * every kind but a logo or icon, and every type but SVG (the API converts the rest itself). */
export async function emailCopyFor(kind: string, file: File): Promise<Blob | null> {
	return EMAIL_COPY_KINDS.has(kind) && isSvg(file) ? svgEmailCopy(file) : null;
}
