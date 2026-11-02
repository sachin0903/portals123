import type { Branding, BrandingPage, ClientPortalTemplate } from '@graphene/auth';
import { defaultPages, slugify } from './portal-pages.js';

const APPEARANCE_KEYS = [
	'colors',
	'radius',
	'font',
	'font_mono',
	'font_display',
	'mode',
	'density',
	'finish',
	'hero_color',
	'hero_feel',
	'glass',
	'card_designs',
	'pages'
] as const satisfies readonly (keyof ClientPortalTemplate)[];

export const MAX_PORTAL_TEMPLATES = 12;

/** A new design set starts as an exact visual copy of the current client portal. */
export function templateFromBranding(
	branding: Branding,
	name: string,
	takenIds: readonly string[] = []
): ClientPortalTemplate {
	const base = slugify(name).replace(/-/g, '_').slice(0, 40) || 'design';
	let id = base;
	let suffix = 1;
	while (takenIds.includes(id)) {
		suffix += 1;
		id = `${base.slice(0, Math.max(1, 40 - String(suffix).length - 1))}_${suffix}`;
	}
	const template: ClientPortalTemplate = {
		id,
		name: name.trim().slice(0, 40),
		role_slugs: [],
		classification_slugs: [],
		group_slugs: []
	};
	for (const key of APPEARANCE_KEYS) {
		const value = branding[key];
		if (value !== undefined) {
			(template as unknown as Record<string, unknown>)[key] = structuredClone(value);
		}
	}
	if (!template.pages?.length) template.pages = defaultPages();
	return template;
}

/** A duplicate is independent all the way down to block settings and colour palettes. */
export function duplicateTemplate(
	source: ClientPortalTemplate,
	takenIds: readonly string[]
): ClientPortalTemplate {
	const copy = templateFromBranding(source, `${source.name} copy`, takenIds);
	copy.role_slugs = [];
	copy.classification_slugs = [];
	copy.group_slugs = [];
	return copy;
}

/** One role can select one design set. Assigning it here removes it from every sibling. */
export function assignTemplateRoles(
	templates: ClientPortalTemplate[],
	templateId: string,
	roleSlugs: string[]
): ClientPortalTemplate[] {
	const assigned = new Set(roleSlugs);
	return templates.map((template) => ({
		...template,
		role_slugs:
			template.id === templateId
				? [...assigned]
				: (template.role_slugs ?? []).filter((role) => !assigned.has(role))
	}));
}

/** One client group can select one design set. Same exclusive assignment as books. */
export function assignTemplateGroups(
	templates: ClientPortalTemplate[],
	templateId: string,
	groupSlugs: string[]
): ClientPortalTemplate[] {
	const assigned = new Set(groupSlugs);
	return templates.map((template) => ({
		...template,
		group_slugs:
			template.id === templateId
				? [...assigned]
				: (template.group_slugs ?? []).filter((slug) => !assigned.has(slug))
	}));
}

/** One classification can select one design set. Same exclusive assignment as roles. */
export function assignTemplateClassifications(
	templates: ClientPortalTemplate[],
	templateId: string,
	classificationSlugs: string[]
): ClientPortalTemplate[] {
	const assigned = new Set(classificationSlugs);
	return templates.map((template) => ({
		...template,
		classification_slugs:
			template.id === templateId
				? [...assigned]
				: (template.classification_slugs ?? []).filter((slug) => !assigned.has(slug))
	}));
}

/** Merge shared firm identity with one selected design for a faithful studio preview. */
export function previewTemplateBranding(
	base: Branding,
	template: ClientPortalTemplate | null
): Branding {
	if (!template) return base;
	const next: Branding = { ...base };
	for (const key of APPEARANCE_KEYS) {
		if (key === 'pages') {
			if (template.pages && template.pages.length > 0) next.pages = template.pages;
			continue;
		}
		delete (next as unknown as Record<string, unknown>)[key];
		if (key in template) (next as unknown as Record<string, unknown>)[key] = template[key];
	}
	delete next.layout;
	const pages = next.pages ?? template.pages;
	const home = pages?.find((page) => page.slug === 'home') ?? pages?.[0];
	if (home) next.layout = home.layouts.default;
	return next;
}

/**
 * Stamp the Pages-view working set onto the payload Save actually POSTs.
 *
 * Working pages live in local studio state so an untouched studio keeps `Branding.pages` null
 * (track the platform defaults). `$state.snapshot(drafts)` has been seen to omit a `pages`
 * property assigned after the draft was created, so the toast could say Saved while the PUT
 * echoed the previous composition. Writing onto a plain object here is the wire shape.
 */
export function withWorkingPages(
	client: Branding,
	workingPages: BrandingPage[] | null | undefined,
	selectedTemplateId: string | null
): Branding {
	if (!workingPages?.length) return client;
	const pages = workingPages;
	const templates = client.templates;
	if (selectedTemplateId && templates?.some((template) => template.id === selectedTemplateId)) {
		const nextTemplates = templates.map((template) =>
			template.id === selectedTemplateId ? { ...template, pages } : template
		);
		const defaultId = client.default_template_id ?? selectedTemplateId;
		return {
			...client,
			templates: nextTemplates,
			...(defaultId === selectedTemplateId ? { pages } : {})
		};
	}
	return { ...client, pages };
}
