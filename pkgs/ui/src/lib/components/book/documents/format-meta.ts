import FileCodeIcon from '@lucide/svelte/icons/file-code';
import FileTextIcon from '@lucide/svelte/icons/file-text';
import ImageIcon from '@lucide/svelte/icons/image';
// From `./display.js` (the real display layer), never from a fixture module.
import type { DocumentRow } from './display.js';

/** Icon + accent colour per stored file format — the file-type badge shared by the documents
 * overview and the client documents tab. Kept out of `documents.ts` (which the server loaders
 * import) so these lucide component imports stay in the client bundle only, mirroring how the
 * activity feed keeps `ACTIVITY_META` component-free with `iconKey` strings. */
export const FORMAT_META: Record<
	DocumentRow['documentType'],
	{ icon: typeof FileTextIcon; cls: string }
> = {
	pdf: { icon: FileTextIcon, cls: 'text-destructive' },
	docx: { icon: FileTextIcon, cls: 'text-info' },
	jpg: { icon: ImageIcon, cls: 'text-success' },
	png: { icon: ImageIcon, cls: 'text-success' },
	html: { icon: FileCodeIcon, cls: 'text-warning' }
};
