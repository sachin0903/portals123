declare module 'grapesjs-blocks-basic' {
	import type { Plugin } from 'grapesjs';
	const plugin: Plugin<{
		flexGrid?: number | boolean;
		blocks?: string[];
		stylePrefix?: string;
		addBasicStyle?: boolean;
		category?: string;
		labelColumn1?: string;
		labelColumn2?: string;
		labelColumn3?: string;
		labelColumn37?: string;
		labelText?: string;
		labelLink?: string;
		labelImage?: string;
		labelVideo?: string;
	}>;
	export default plugin;
}
