import type { Component } from 'svelte';
import SettingsIcon from '@lucide/svelte/icons/settings';
import KeyRoundIcon from '@lucide/svelte/icons/key-round';
import NetworkIcon from '@lucide/svelte/icons/network';
import HardDriveIcon from '@lucide/svelte/icons/hard-drive';
import MailIcon from '@lucide/svelte/icons/mail';
import PlugIcon from '@lucide/svelte/icons/plug';

export interface SettingsSection {
	label: string;
	href: string;
	icon: Component<{ class?: string }>;
}

/**
 * The sub-nav of a firm's Settings page (`routes/tenants/[uid]/settings/+layout.svelte`).
 *
 * Pure so the gates can be pinned: General, Sign-in, Platforms, API and Sending are the
 * firm-configuration surface (`tenants:read`, or `model:read` for the model tenant — the layout's
 * `canConfigure`); Storage browses the firm's bucket, which is a book read (`book:read`) and its
 * own loader refuses anyone else. A section a principal cannot open is not offered.
 */
export function settingsSections(
	base: string,
	{ canConfigure, canBrowseStorage }: { canConfigure: boolean; canBrowseStorage: boolean }
): SettingsSection[] {
	return [
		...(canConfigure
			? [
					{ label: 'General', href: base, icon: SettingsIcon },
					{ label: 'Sign-in', href: `${base}/sign-in`, icon: KeyRoundIcon },
					{ label: 'Platforms', href: `${base}/platforms`, icon: NetworkIcon },
					{ label: 'API', href: `${base}/api`, icon: PlugIcon },
					{ label: 'Sending', href: `${base}/sending`, icon: MailIcon }
				]
			: []),
		...(canBrowseStorage
			? [{ label: 'Storage', href: `${base}/storage`, icon: HardDriveIcon }]
			: [])
	];
}
