import type { Component } from 'svelte';
import FilePenLineIcon from '@lucide/svelte/icons/file-pen-line';
import ChartLineIcon from '@lucide/svelte/icons/chart-line';
import FileIcon from '@lucide/svelte/icons/file';
import FileTextIcon from '@lucide/svelte/icons/file-text';
import LandmarkIcon from '@lucide/svelte/icons/landmark';
import MailIcon from '@lucide/svelte/icons/mail';

/**
 * One mark per document category, shared by every surface that draws one (the rows, the
 * category tiles) so a client sees the same picture for "Statements" wherever it appears.
 * Typed by the wire contract, but the server can drift ahead of the client — a category this
 * build has never heard of still gets the generic mark, never an undefined component.
 */
const DOCUMENT_ICONS: Record<string, Component> = {
	Agreements: FilePenLineIcon,
	Illustrations: ChartLineIcon,
	Statements: FileTextIcon,
	Tax: LandmarkIcon,
	Correspondence: MailIcon
};

export function documentIcon(category: string): Component {
	return DOCUMENT_ICONS[category] ?? FileIcon;
}
