import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Invariants of the app CHROME (sidebar + topbar) that no type gate can see.
 *
 * svelte-check never compiles a class string, so every assertion here describes something that broke
 * silently in review at least once: a divider that stopped lining up because one of the two bars
 * changed height, a corner that went back to tracking the full branded `--radius`, the client
 * portal's glass being "tidied up" into the staff-tool treatment, or — the worst of them — a live
 * `ThemeToggle` inside the branding PREVIEW, re-theming the admin portal it was rendered in.
 */
const SHELL = import.meta.dirname;
const BRANDING = join(SHELL, '..', 'branding');

/** Strip comments — HTML, block AND `//` line comments (the `:` lookbehind spares `https://`) —
 * so prose naming a class ("replaces `glass-bar`") doesn't satisfy or trip a match. */
const stripComments = (code: string) =>
	code
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/(?<!:)\/\/[^\n]*/g, '');

const read = (name: string) => stripComments(readFileSync(join(SHELL, name), 'utf8'));

const sidebar = read('app-sidebar.svelte');
const topbar = read('saas-topbar.svelte');
const clientShell = read('client-shell.svelte');
const saasShell = read('saas-shell.svelte');
const sidebarUser = read('sidebar-user.svelte');
const sidebarContext = read('sidebar-context.svelte');
const shellPreview = stripComments(readFileSync(join(BRANDING, 'shell-preview.svelte'), 'utf8'));
const provider = stripComments(
	readFileSync(join(SHELL, '..', 'ui', 'sidebar', 'sidebar-provider.svelte'), 'utf8')
);
const menuButton = stripComments(
	readFileSync(join(SHELL, '..', 'ui', 'sidebar', 'sidebar-menu-button.svelte'), 'utf8')
);

/** The `h-…` in the first class attribute of the given opening tag (literal or arbitrary value). */
function heightClass(code: string, tag: string): string | undefined {
	const open = new RegExp(`<${tag}\\b[^>]*?class="([^"]*)"`, 's').exec(code);
	return open?.[1].match(/\bh-(?:\d+|\[[^\]]+\])/)?.[0];
}

/**
 * The `{#if}` conditions wrapping the first occurrence of `needle`, outermost first.
 *
 * An `{:else}` branch is reported as `!(cond)`, so `{#if !interactive}…{:else}HERE{/if}` yields
 * `!(!interactive)`. Used to prove a control is genuinely behind the `interactive` guard rather than
 * merely mentioned somewhere in the same file.
 */
function guardsAt(code: string, needle: string): string[] {
	const at = code.indexOf(needle);
	expect(at, `"${needle}" not found`).toBeGreaterThan(-1);
	const stack: { cond: string; inElse: boolean }[] = [];
	const blocks = /\{#if\s+([^}]*)\}|\{:else if\s+([^}]*)\}|\{:else\}|\{\/if\}/g;
	for (let m = blocks.exec(code); m && m.index < at; m = blocks.exec(code)) {
		if (m[1] !== undefined) stack.push({ cond: m[1].trim(), inElse: false });
		else if (m[2] !== undefined) stack[stack.length - 1] = { cond: m[2].trim(), inElse: false };
		else if (m[0] === '{:else}') stack[stack.length - 1].inElse = true;
		else stack.pop();
	}
	return stack.map((f) => (f.inElse ? `!(${f.cond})` : f.cond));
}

/** True when `needle` only renders in the interactive (real-app) branch. */
const onlyWhenInteractive = (code: string, needle: string) =>
	guardsAt(code, needle)
		.map((g) => g.replace(/^!\(!(.*)\)$/, '$1')) // `!(!interactive)` ⇒ `interactive`
		.some((g) => g === 'interactive');

describe('SaaS chrome (platform + admin)', () => {
	it('draws the sidebar header and the topbar at the same height', () => {
		// The two bottom hairlines have to meet at the sidebar's edge to read as ONE divider across
		// the app. They are set in different files, so nothing but this test couples them.
		const header = heightClass(sidebar, 'Sidebar\\.Header');
		const bar = heightClass(topbar, 'header');
		expect(header).toBeDefined();
		expect(bar).toBe(header);
	});

	it('takes that height from --topbar-h, declared once by the shell', () => {
		// Raising the bar 3.5rem → 4rem broke three full-height platform editors that had hardcoded
		// `calc(100svh-3.5rem)`. The height is a token so a page can subtract it instead of guessing;
		// this test is what stops a literal `h-16` creeping back in and desynchronising them again.
		expect(saasShell).toMatch(/--topbar-h:\s*4rem/);
		for (const [name, code] of [
			['app-sidebar', sidebar],
			['saas-topbar', topbar]
		] as const) {
			expect(heightClass(code, name === 'app-sidebar' ? 'Sidebar\\.Header' : 'header'), name).toBe(
				'h-[var(--topbar-h,4rem)]'
			);
		}
		// The fallback is not decoration: the mobile sidebar renders in a body-portaled sheet, outside
		// the element that declares the var.
		expect(topbar).toMatch(/var\(--topbar-h,4rem\)/);
	});

	it('declares --radius-chrome in both chrome roots', () => {
		// The nav rows, account row, avatar, org chip and theme toggle all resolve
		// `rounded-[var(--radius-chrome)]` by INHERITANCE. If a root stops declaring it the var is
		// undefined, border-radius becomes invalid at computed-value time and the corners silently
		// collapse to 0 — which no gate reports.
		for (const [name, code] of [
			['app-sidebar', sidebar],
			['saas-topbar', topbar]
		] as const) {
			expect(code, name).toMatch(/\[--radius-chrome:min\(var\(--radius\),\s*[\d.]+rem\)\]/);
		}
	});

	it('resolves every --radius-chrome consumer against a declaring root', () => {
		// Every file that consumes the var is rendered inside app-sidebar's or saas-topbar's subtree.
		// A new consumer outside both (e.g. a portalled dropdown body) would resolve to nothing.
		const consumers = [
			'app-sidebar.svelte',
			'saas-topbar.svelte',
			'sidebar-user.svelte',
			'tenant-switcher.svelte'
		];
		const declares = /\[--radius-chrome:/;
		for (const name of consumers) {
			const code = read(name);
			if (!/var\(--radius-chrome\)/.test(code)) continue;
			const inChromeSubtree =
				declares.test(code) || declares.test(sidebar) || declares.test(topbar);
			expect(inChromeSubtree, name).toBe(true);
		}
	});

	it('keeps the topbar off the glass treatment', () => {
		// `glass-bar` resolves to `--glass-bg`, which the sharp preset makes identical to the page
		// canvas — the bar dissolves into the content. Staff chrome shares the sidebar's surface.
		expect(topbar).not.toMatch(/\bglass-bar\b/);
		expect(topbar).toMatch(/\bbg-sidebar\b/);
	});

	it('keeps search in the trailing utility cluster beside page actions', () => {
		// The trigger only opens a palette; it is not an input and must not spend the centre of every
		// page pretending to be one. Search comes first in the same non-growing cluster as the
		// notification bell and tenant switcher.
		const cluster = topbar.match(
			/<div data-slot="topbar-actions"[\s\S]*?<\/div>\s*<\/header>/
		)?.[0];
		expect(cluster).toBeDefined();
		expect(cluster).toContain('{#if search}{@render search()}{/if}');
		expect(cluster).toContain('{#if actions}{@render actions()}{/if}');
		expect(cluster!.indexOf('render search')).toBeLessThan(cluster!.indexOf('render actions'));
		expect(topbar).not.toMatch(/max-w-xl/);
	});

	it('states the active nav row with more than a tinted fill', () => {
		// `--sidebar-accent` is ~4% off `--sidebar`; on its own it reads as a smudge without the glass
		// glow. The leading rule and the primary-coloured icon are what make it definite.
		expect(sidebar).toMatch(/data-active:shadow-\[inset_2px_0_0_0_var\(--primary\)\]/);
		expect(sidebar).toMatch(/data-active:\[&_svg\]:text-primary/);
	});
});

describe('instant navigation feedback', () => {
	const navProgress = read('nav-progress.svelte');

	it('drives every affordance from one pending path', () => {
		// SvelteKit renders the page you are LEAVING for the whole of the destination's `load`, so
		// `pathname` is the old page until the API answers and a sidebar click bought no visible
		// change at all. One value — the app's `navigating.to` — feeds the bar, the highlight and the
		// dim, so they cannot disagree about whether a navigation is in flight.
		expect(saasShell).toMatch(/pendingHref = null/);
		expect(saasShell).toMatch(/<NavProgress active=\{pending\} \/>/);
		expect(saasShell).toMatch(/<AppSidebar[\s\S]*?\{pendingHref\}/);
		expect(sidebar).toMatch(/pendingHref = null/);
		expect(clientShell).toMatch(/pendingHref = null/);
		expect(clientShell).toMatch(/<NavProgress active=\{interactive && pendingHref !== null\}/);
	});

	it('lights the destination row on the click, not when the load answers', () => {
		// The whole point: the highlight matches where we are GOING while a navigation is in flight.
		// A stray `pathname` comparison left in the matcher silently reverts it to the old behaviour.
		expect(sidebar).toMatch(/targetPath = \$derived\(pendingHref \?\? pathname\)/);
		expect(sidebar).toMatch(/targetPath === item\.href/);
		expect(sidebar).not.toMatch(/pathname === item\.href/);
		expect(clientShell).toMatch(/isNavActive\(href, targetPath\)/);
		expect(clientShell).not.toMatch(/isNavActive\(href, pathname\)/);
	});

	it('marks the lit row as still loading', () => {
		// A row can be active before its page exists, so something has to distinguish "arrived" from
		// "on its way" — otherwise a slow load reads as a page that rendered nothing. It takes
		// precedence over the unread dot, which shares the same slot.
		expect(sidebar).toMatch(/\{#if pending\}/);
		expect(sidebar).toMatch(/animate-spin/);
		expect(sidebar.indexOf('const pending = isPending(item.href)')).toBeLessThan(
			sidebar.indexOf('const unread = item.unread && !isActive(item.href)')
		);
		// Both pictures put the spinner first, so a slow load never shows a dot in one and a
		// spinner in the other.
		for (const each of [
			/\{#if pending\}[\s\S]*?\{:else if unread\}/,
			/\{#if pending\}[\s\S]*?\{:else\}/
		]) {
			expect(sidebar).toMatch(each);
		}
	});

	it('keeps the progress animation visible to the compiler', () => {
		// The class is a literal in the markup, never composed through `cn()`: Svelte prunes a scoped
		// style whose selector it cannot see, and a pruned animation leaves a static sliver that every
		// other assertion here still passes.
		expect(navProgress).toMatch(/class="nav-progress /);
		expect(navProgress).toMatch(/@keyframes nav-progress\b/);
		expect(navProgress).toMatch(/animation: nav-progress /);
		// Reduced motion keeps the SIGNAL and drops the movement — it is the answer to "did my click
		// land", so suppressing it entirely would leave that reader with nothing.
		const reduced = navProgress.slice(navProgress.indexOf('prefers-reduced-motion'));
		expect(reduced).toMatch(/animation: none/);
		expect(reduced).toMatch(/transform: scaleX/);
	});

	it('reserves the content dim for a real change of page', () => {
		// A listing's search box and its pager navigate to the SAME path. Dimming the canvas on every
		// debounced keystroke reads as a fault, so only a different pathname dims what is being left.
		expect(saasShell).toMatch(/leaving = \$derived\(pending && pendingHref !== pathname\)/);
		expect(saasShell).toMatch(/style:opacity=\{leaving \?/);
		// Style DIRECTIVES, not a class: a scoped rule hidden inside `cn()` would be pruned, and the
		// transition delay is what stops a fast navigation flashing.
		expect(saasShell).toMatch(/style:transition=\{leaving \? 'opacity 150ms ease 80ms'/);
		expect(saasShell).toMatch(/aria-busy=\{leaving \? 'true' : undefined\}/);
	});

	it('shows no navigation feedback inside a preview', () => {
		// A preview is a picture of the app and never navigates — the same rule as the theme toggle,
		// the rail cookie and the sign-out form.
		expect(saasShell).toMatch(/pending = \$derived\(interactive && pendingHref !== null\)/);
		expect(clientShell).toMatch(
			/targetPath = \$derived\(interactive \? \(pendingHref \?\? pathname\)/
		);
	});

	it('closes the mobile sheet when a nav destination is chosen', () => {
		// The sheet is owned by the root layout, so it survives every client-side navigation.
		// Without an explicit close, the next page is trapped behind the overlay — the click
		// that left was the only chance to dismiss it. The handler lives on the sheet itself
		// so the lockup, the focused-record back link and the account menu are covered too;
		// a `$effect` here would fight the test that nav open-state must not restore from one.
		const mobileSheet = stripComments(
			readFileSync(join(SHELL, '..', 'ui', 'sidebar', 'sidebar.svelte'), 'utf8')
		);
		expect(mobileSheet).toMatch(/closest\('a\[href\]'\)/);
		expect(mobileSheet).toMatch(/setOpenMobile\(false\)/);
	});
});

describe('sidebar header lockup', () => {
	const label = 'text-muted-foreground/80 text-[0.7rem]';

	it('does not print the portal name under the logo unless asked', () => {
		// "Platform" under a wordmark in a firm's own platform portal tells the reader nothing they
		// did not already know, and it cost a two-line header that pinned the logo at 24px. It stays
		// available for the admin portal, where the string IS the distinguishing signal.
		expect(sidebar).toMatch(/portalLabel = false/);
		expect(guardsAt(sidebar, label)).toContain('portalLabel');
	});

	it('still announces the portal to a screen reader', () => {
		// The label went from the picture, not from the accessible name — otherwise two portals that
		// wear the same tenant logo become indistinguishable to anyone not reading the nav.
		expect(sidebar).toMatch(/aria-label="\{brandName\} \{portal\} — home"/);
	});

	it('grows BOTH halves of the lockup when the label is gone', () => {
		// A taller mark beside a fixed 1.125rem wordmark reads as a misdrawn logo rather than a
		// larger one, and `Logo` hardcoded that text size — hence `nameClass`. Passing one without
		// the other is the failure this pins.
		expect(sidebar).toMatch(/markClass=\{portalLabel \? 'h-6 shrink-0' : 'h-9 shrink-0'\}/);
		expect(sidebar).toMatch(/nameClass=\{portalLabel \? undefined : 'text-xl'\}/);
		const logo = stripComments(readFileSync(join(SHELL, '..', 'brand', 'logo.svelte'), 'utf8'));
		expect(logo).toMatch(/cn\(\s*'text-lg[^']*',\s*nameClass\s*\)/);
	});
});

describe('collapsed icon rail', () => {
	it('gives the rail its own mark instead of shrinking the lockup', () => {
		// The rail is 3.5rem wide. `object-contain` on a 4:1 tenant wordmark in ~24px of usable width
		// renders it about 6px tall — illegible. The lockup is hidden and a square mark takes over.
		expect(sidebar).toMatch(/group-data-\[collapsible=icon\]:hidden[\s\S]*?<Logo/);
		expect(sidebar).toMatch(/hidden group-data-\[collapsible=icon\]:flex/);
	});

	it('drops the header side padding when collapsed', () => {
		// 3.5rem − 2×0.75rem left 32px for the mark; the rail centres it in the full 56px instead.
		expect(heightClass(sidebar, 'Sidebar\\.Header')).toBeDefined();
		expect(sidebar).toMatch(/group-data-\[collapsible=icon\]:px-0/);
	});

	it('falls back to a monogram when the firm has a wordmark but no symbol', () => {
		// Logo's own `iconSrc ?? src` fallback cannot serve this case — it hands back the wordmark,
		// which is the illegible thing. Only a firm with NO assets gets the built-in Graphene mark.
		expect(sidebar).toMatch(/railIcon\s*=\s*\$derived\(/);
		expect(sidebar).toMatch(/brand\?\.iconUrl\s*\?\s*'symbol'/);
		expect(sidebar).toMatch(/brand\?\.logoUrl\s*\|\|\s*brand\?\.logoDarkUrl\s*\?\s*'monogram'/);
		expect(sidebar).toMatch(/bg-sidebar-primary/);
	});

	it('keeps the unread dot and the loading spinner OUT of the clipped rail button', () => {
		// The rail button is `size-9 p-2 overflow-hidden` around a `size-4` icon: an `ml-auto` child
		// has nowhere to go and is clipped away entirely, so collapsing the sidebar used to delete
		// both indicators outright — in the one state where the rail is all the reader has. Each is
		// rendered twice: the inline one hidden in the rail, and a corner badge on the MenuItem
		// (which is `relative`), outside the button and therefore outside the clip.
		const inline = sidebar.match(/ml-auto[^"]*group-data-\[collapsible=icon\]:hidden/g);
		expect(inline, 'both inline indicators must hide in the rail').toHaveLength(2);

		// …and the rail pair sits after the button closes, still inside the MenuItem.
		const tail = sidebar.slice(
			sidebar.indexOf('</Sidebar.MenuButton>'),
			sidebar.indexOf('</Sidebar.MenuItem>')
		);
		expect(tail).toMatch(/absolute[^"]*hidden[^"]*group-data-\[collapsible=icon\]:grid/);
		expect(tail).toMatch(/LoaderCircleIcon/);
		// Ringed in the sidebar's own ground so it reads where it overlaps the glyph, and never
		// intercepts the click meant for the row beneath it.
		expect(tail).toMatch(/ring-sidebar/);
		expect(tail).toMatch(/pointer-events-none/);
	});

	it('draws a hairline wherever the expanded sidebar prints a heading', () => {
		// Group labels and subsection headings are both text, and text has no room at 3.5rem — so the
		// rail hid all of it and rendered twenty icons as one undifferentiated column. The rule is
		// that the rail keeps the boundary and drops only the words.
		// A <div> among Sidebar.Content's divs; an <li> inside Sidebar.Menu's <ul>. Rendering the
		// wrong one is invalid markup that nothing else here would catch.
		expect(sidebar).toMatch(/\{#snippet railRule\(\)\}\s*<div/);
		expect(sidebar).toMatch(/\{#snippet railRuleItem\(\)\}\s*<li/);
		expect(sidebar).toMatch(/\{@render railRuleItem\(\)\}/);

		// Derived, not hand-placed. Between groups it comes from `railRuleKeys`, which skips a group
		// that renders nothing — position alone drew a hairline above the closed Firm settings group,
		// floating just under the account footer with no icons beneath it.
		expect(sidebar).toMatch(/\{#if ruleKeys\.has\(key\)\}\{@render railRule\(\)\}\{\/if\}/);
		expect(sidebar).toMatch(/railRuleKeys\(\[\.\.\.mainGroups, \.\.\.pinnedGroups\]\)/);
		// Inside a group it comes off the same `{#if}` as the subsection heading itself.
		expect(sidebar).toMatch(/\{item\.section\}[\s\S]{0,120}\{@render railRuleItem\(\)\}/);

		// ONE look, read by both snippets. A copy each is how `field-shell.ts` records controls
		// drifting apart; rail-only, because in the open sidebar the words do the job.
		//
		// Tinted in `--primary`, the active tile's own token, rather than the neutral `--glass-border`
		// the header and footer bound the chrome with: the rail's rules divide the nav, and a firm's
		// own primary drives both signals.
		expect(sidebar).toMatch(
			/const RAIL_RULE_CLASS =\s*'[^']*hidden h-px w-7 shrink-0 bg-primary\/30 group-data-\[collapsible=icon\]:block'/
		);
		expect(sidebar.match(/class=\{RAIL_RULE_CLASS\}/g)).toHaveLength(2);
	});

	it('draws one badge box in the rail, not two that drift apart', () => {
		// Given their own offsets the corner badge JUMPED as a pending navigation resolved into an
		// unread dot, and only one of the two shapes had the `--sidebar` separation from the glyph
		// under it. One positioned box, sized to the larger shape, contents centred.
		const tail = sidebar.slice(
			sidebar.indexOf('</Sidebar.MenuButton>'),
			sidebar.indexOf('</Sidebar.MenuItem>')
		);
		expect(tail.match(/absolute/g), 'exactly one positioned badge').toHaveLength(1);
		expect(tail).toMatch(/place-items-center/);
	});

	it('computes each indicator once and reads it in both pictures', () => {
		// Written out twice, the inline indicator and the rail badge could be given different
		// suppression rules, and a test that counts class strings would never see it.
		expect(sidebar).toMatch(/\{@const pending = isPending\(item\.href\)\}/);
		expect(sidebar).toMatch(/\{@const unread = item\.unread && !isActive\(item\.href\)\}/);
		expect(sidebar).not.toMatch(/\{:else if item\.unread && !isActive/);
	});

	it('does not force every group open just because the sidebar is collapsed', () => {
		// The rail used to force every collapsible group open, so collapsing the sidebar ADDED the
		// fourteen rows of a `defaultOpen: false` Firm settings group — the compact chrome showing
		// strictly more than the full one. `isGroupOpen` takes no rail flag any more.
		//
		// This is deliberately NOT "the rail shows the same groups as the open sidebar": two rail
		// overrides remain, each covering a case where mirroring costs the reader something the open
		// sidebar never charges them, and both are asserted below.
		expect(sidebar).not.toMatch(/isGroupOpen\([^)]*railMode/);
	});

	it('keeps exclusivity out of the rail, and says why', () => {
		// A `solo` group shuts the others so a fourteen-row panel does not push them off a short
		// viewport. The rail has no such problem, and applying it there would delete Dashboard,
		// Clients and Accounts from the icon column the whole app is navigated by.
		expect(sidebar).toMatch(/soloGroupOpen\(nav, openState, railMode\)/);
	});

	it('opens the group holding the page the reader is on', () => {
		// The other rail override, and the one that pays for the corner badges below: without it the
		// active item can sit in a group that renders nothing in the rail, so there is no lit tile,
		// no spinner and no dot — and no label to say the section is even there.
		expect(sidebar).toMatch(/railMode && groupHoldsHref\(group, activeHref\)/);
	});

	it('resolves each group open state once, for every reader of it', () => {
		// The disclosure's `open`, whether the group renders, and whether a rule is drawn above it
		// are three readers of one answer. Computing it per consumer is what drew a hairline above a
		// group that renders nothing.
		expect(sidebar).toMatch(/const resolved = \$derived\(/);
		expect(sidebar).toMatch(/const mainGroups = \$derived\(resolved\.filter/);
		expect(sidebar).toMatch(/const pinnedGroups = \$derived\(resolved\.filter/);
		expect(sidebar).toMatch(/\{@render navGroup\(group, key, open\)\}/);
		expect(sidebar).toMatch(/<Collapsible\.Root\s+\{open\}/);
	});

	it('centres the rail on one axis instead of insetting it twice', () => {
		// A 3.5rem rail holds a hard `size-9!` 36px tile with 20px to spare, and Content's `px-2` plus
		// each Group's `px-2` consume 32px of it. The tile then overflows its box to the right and
		// sits off the centre line, while every `mx-auto` hairline centres honestly — the two
		// disagree, and the icons read as shifted.
		expect(sidebar).toMatch(/class="px-2 pt-2 group-data-\[collapsible=icon\]:px-0"/);
		const groups = sidebar.match(
			/<Sidebar\.Group class="py-1 group-data-\[collapsible=icon\]:px-0">/g
		);
		expect(groups, 'both Sidebar.Group branches drop the rail inset').toHaveLength(2);
		// With the inset gone the <li> must shrink to the tile and centre, or it stretches to 56px
		// and the 36px tile is left-aligned inside it — off centre the other way.
		expect(sidebar).toMatch(
			/<Sidebar\.Menu class="gap-0\.5 group-data-\[collapsible=icon\]:items-center">/
		);
	});

	it('gives the rail the same highlight as the open row, not a second design', () => {
		// One indicator in two widths: the leading `--primary` rule and the `--primary` icon are
		// declared once and apply in both states — the rail must NOT cancel the rule, or collapsing
		// the sidebar swaps the highlight for a different one.
		expect(sidebar).not.toMatch(/group-data-\[collapsible=icon\]:data-active:shadow-none/);
		expect(sidebar).not.toMatch(/group-data-\[collapsible=icon\]:data-active:ring-/);
		// Only the FILL differs, and only in strength: a 36px square carries no label, so the tint is
		// stated in `--primary` where the open row can lean on `--sidebar-accent` behind its words.
		expect(sidebar).toMatch(/group-data-\[collapsible=icon\]:data-active:bg-primary\/12/);
		// …and it still answers a hover. The tint compiles to two classes plus two attributes, which
		// outranks the primitive's plain `hover:bg-sidebar-accent` — so without its own hover rule the
		// active tile is the one row in the rail that does nothing under the pointer.
		expect(sidebar).toMatch(/group-data-\[collapsible=icon\]:data-active:hover:bg-primary\/20/);
	});

	it('takes the rail label out of the layout instead of merely clipping it', () => {
		// The button is `overflow-hidden`, so a 36px tile LOOKS like an icon on its own — but the
		// label is still laid out beside it, and `gap-2.5` is still spent on it, which pushed every
		// glyph a few pixels left of the tile's centre line while every `mx-auto` hairline centred
		// honestly. `sr-only` is the fix and `hidden` is not: the rail button's only text IS the
		// label, so removing it from the accessibility tree leaves the link with no name.
		expect(sidebar).toMatch(/group-data-\[collapsible=icon\]:sr-only/);
		expect(sidebarContext).toMatch(/group-data-\[collapsible=icon\]:sr-only/);
		const labelClass = /\{item\.label\}/.test(sidebar);
		expect(labelClass, 'the nav label is still printed in the open sidebar').toBe(true);
	});

	it('centres the tile on the button itself, not on its parent alone', () => {
		// `items-center` on the nav menu centres the <li>, and it is the only thing that did: the
		// account row and the focused-record back row sit in their own menus, which do not set it, so
		// their tiles hugged the left edge while the nav icons above them centred. `mx-auto` on the
		// collapsed button is the answer for all three at once — a no-op wherever the parent already
		// centres, and the whole of it where nothing does.
		expect(menuButton).toMatch(/group-data-\[collapsible=icon\]:mx-auto/);
		// The glyph then centres INSIDE the tile: 36px around a 16px icon leaves 20px of content box,
		// which a left-aligned flex row puts 2px off the tile's own centre line.
		expect(menuButton).toMatch(/group-data-\[collapsible=icon\]:justify-center/);
	});
});

describe('collapsible + pinned nav groups', () => {
	it('builds the disclosure out of the Collapsible primitive, not a hand-rolled button', () => {
		// The trigger has to own `aria-expanded`, `aria-controls`, `type="button"` and Space/Enter. A
		// raw <button> + a boolean gets the visuals right and leaves the section unreachable by
		// keyboard, which is precisely the kind of thing the design language exists to prevent.
		expect(sidebar).toMatch(/<Collapsible\.Root/);
		expect(sidebar).toMatch(/<Collapsible\.Trigger/);
		expect(sidebar).toMatch(/<Collapsible\.Content/);
		expect(sidebar).not.toMatch(/<button\b/);
	});

	it('renders the group label AS the trigger', () => {
		// Not a second control beside the label: the whole 16rem-wide label row is the hit target.
		expect(sidebar).toMatch(/<Sidebar\.GroupLabel[\s\S]*?<Collapsible\.Trigger \{\.\.\.props\}/);
	});

	it('drives `open` rather than binding it', () => {
		// A `solo` group suppresses the others, so what a group DISPLAYS and what the user asked of
		// it deliberately differ — and a binding would collapse the two, writing the suppression into
		// the persisted map and losing the state it exists to restore. The rail's active-group
		// override is the same hazard: it would persist, and the group would stay open for good.
		expect(sidebar).toMatch(/<Collapsible\.Root\s+\{open\}/);
		expect(sidebar).toMatch(/isGroupOpen\(group, openState, key, soloOpen\)/);
		expect(sidebar).not.toMatch(/bind:open/);
		expect(sidebar).toMatch(/onOpenChange=\{\(open\) => setGroupOpen\(key, open\)\}/);
		// One writer: every change goes through the pure rule (`nav-groups.applyGroupOpen`), never
		// straight into the map — that is where "opening a normal group closes the solo one" lives.
		expect(sidebar).toMatch(/applyGroupOpen\(nav, openState, key, open\)/);
	});

	it('excludes mobile from rail mode', () => {
		// On mobile the sidebar is a full-width portaled sheet, but `sidebar.state` still reports the
		// DESKTOP rail state. Without `!isMobile` a phone would render the labelless rail treatment at
		// 18rem wide.
		expect(sidebar).toMatch(
			/railMode\s*=\s*\$derived\(\s*sidebar\.state === 'collapsed' && !sidebar\.isMobile/
		);
	});

	it('pins a pinned group below a flexible spacer, above the footer', () => {
		const content = sidebar.slice(
			sidebar.indexOf('<Sidebar.Content'),
			sidebar.indexOf('</Sidebar.Content>')
		);
		expect(content).toMatch(/pinnedGroups\.length > 0/);
		// `flex-1` on the spacer, and `min-h-2` so it never collapses the gap to nothing when the nav
		// is exactly viewport height.
		expect(content).toMatch(/class="min-h-2 flex-1"/);
		// Spacer, then pinned groups — order matters, that IS the pinning.
		expect(content.indexOf('min-h-2 flex-1')).toBeLessThan(content.indexOf('pinnedGroups as'));
		// And the account row stays last.
		expect(sidebar.indexOf('</Sidebar.Content>')).toBeLessThan(sidebar.indexOf('<Sidebar.Footer'));
	});

	it('restores the open map at init, not from a post-mount effect', () => {
		// An effect would render the declared `defaultOpen` first and snap to the stored state after
		// hydration — a visible flash on every hard reload. Reading at init means hydration IS the
		// restore. It only works because the primitive keeps the closed panel mounted (one `hidden`
		// attribute apart), so there is no structural hydration mismatch to trip over.
		expect(sidebar).toMatch(
			/\$state<Record<string, boolean>>\(\s*untrack\(\(\) => \(interactive \? readNavState\(portal\)/
		);
		expect(sidebar).not.toMatch(/\$effect\(/);
	});

	it('keeps the open map per portal, and out of the branding preview', () => {
		expect(sidebar).toMatch(/localStorage\.setItem\(navStateKey\(portal\)/);
		// Read: `interactive ? readNavState(portal) : {}` (asserted above). Write: an early return
		// immediately before it. A preview sidebar must neither inherit nor rewrite the host app's
		// chrome preferences — same rule as the rail cookie and the theme toggle.
		expect(sidebar).toMatch(/if \(!interactive\) return;\s*try \{\s*localStorage\.setItem/);
	});
});

describe('the focused-record sidebar (NavContext)', () => {
	it('sits above the nav, inside the sidebar', () => {
		expect(sidebar).toMatch(/\{#if context\}[\s\S]*?<SidebarContext/);
		expect(sidebar.indexOf('<SidebarContext')).toBeLessThan(sidebar.indexOf('<Sidebar.Content'));
	});

	it('hides the three-line identity in the rail and shows a monogram instead', () => {
		// 56px cannot hold a name, an email and an eyebrow; shrinking them yields three ellipses.
		expect(sidebarContext).toMatch(/group-data-\[collapsible=icon\]:hidden/);
		expect(sidebarContext).toMatch(
			/hidden justify-center[\s\S]*?group-data-\[collapsible=icon\]:flex/
		);
	});

	it('makes the rail monogram an indicator, not a dead control', () => {
		// A tile that looks like a button and does nothing is worse than no tile.
		expect(sidebarContext).toMatch(/role="img"/);
		// Labelled in the rail, where it stands alone; decorative in the expanded header, where the
		// three text lines beside it already say the same thing.
		expect(sidebarContext).toMatch(/aria-label=\{labelled \? railLabel : undefined\}/);
		expect(sidebarContext).toMatch(/aria-hidden=\{labelled \? undefined : true\}/);
	});

	it('draws a firm mark in the tile before the glyph, before the monogram', () => {
		// The admin portal drilled into a firm passes that firm's own icon; a firm with none set gets
		// a glyph (a building), and only a record with neither falls back to initials. Letterboxed,
		// not cropped: a firm's icon need not be square.
		expect(sidebarContext).toMatch(
			/\{#if context\.image\}\s*<img src=\{context\.image\} alt="" class="[^"]*object-contain[^"]*"/
		);
		expect(sidebarContext).toMatch(/\{:else if context\.icon\}[\s\S]*?<Icon class="size-4" \/>/);
		expect(sidebarContext).toMatch(/\{:else\}\s*\{monogram\}/);
	});

	it('shows the mark in the expanded header only when there is one', () => {
		// A person's record does not grow a second avatar in the chrome — the record page owns it.
		expect(sidebarContext).toMatch(/\{#if hasMark\}\s*\{@render tile\(false\)\}/);
		expect(sidebarContext).toMatch(
			/hasMark = \$derived\(Boolean\(context\.image \|\| context\.icon\)\)/
		);
		// The rail always renders the tile, labelled.
		expect(sidebarContext).toMatch(
			/group-data-\[collapsible=icon\]:flex">\s*\{@render tile\(true\)\}/
		);
	});

	it('tints the record tile off the brand ramp, not the header mark colour', () => {
		// `--sidebar-primary` is the brand lockup's colour; reusing it makes the record read as a
		// second brand. Tonal ramp tokens only — no palette classes, no hex.
		expect(sidebarContext).toMatch(/bg-brand-subtle text-brand-strong border-brand-line/);
		expect(sidebarContext).not.toMatch(/bg-sidebar-primary/);
		expect(sidebarContext).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
	});

	it('continues the header divider with the same token', () => {
		// The band is bounded by `--glass-border`, the same var the sidebar header and the topbar use, so
		// the three hairlines read as one system rather than three different greys.
		expect(sidebarContext).toMatch(/border-b \[border-bottom-color:var\(--glass-border\)\]/);
	});

	it('draws the record monogram with personInitials, not initials', () => {
		// "Mr Max Oakden" ⇒ MO, agreeing with the avatar on the record's own page. Plain `initials`
		// gives MM (the honorific), which put two different monograms for one person on one screen.
		expect(sidebarContext).toMatch(/personInitials\(context\.title, '·'\)/);
	});

	it('keeps the back link reachable in the rail', () => {
		// The only escape from a focused sidebar. As a real nav row it inherits the icon + tooltip
		// treatment, so it survives the collapse.
		expect(sidebarContext).toMatch(/<Sidebar\.MenuButton\s+tooltipContent=\{back\.label\}/);
		expect(sidebarContext).toMatch(/href=\{interactive \? back\.href : undefined\}/);
	});
});

describe('the branding preview is a picture, not a second app', () => {
	it('renders both shells non-interactively, inside the isolating iframe frame', () => {
		// Two layers, both required: the props keep app-mutating nodes out of the DOM, and the
		// PreviewFrame iframe is the browser-enforced backstop — a separate document, so nothing
		// inside can touch the host app, plus capture-phase activation suppression (see
		// branding/preview-frame.test.ts for that component's own invariants). The old `inert`
		// attribute must NOT come back: it killed scrolling, the one capability the preview needs.
		expect(shellPreview).toMatch(/<SaaSShell[\s\S]*?interactive=\{false\}/);
		expect(shellPreview).toMatch(/<ClientShell[\s\S]*?interactive=\{false\}/);
		expect(shellPreview).toMatch(/<PreviewFrame\b/);
		expect(shellPreview).not.toMatch(/\binert\b/);
	});

	it('keeps theme management out of the topbar and out of a preview account menu', () => {
		// Theme now belongs with the logged-in user at the bottom-left. A preview still cannot mount
		// it: selecting there would write `gp-theme` + `.dark` on the host document.
		expect(topbar).not.toContain('<ThemeToggle');
		expect(onlyWhenInteractive(sidebarUser, '<ThemeToggle')).toBe(true);
		expect(onlyWhenInteractive(topbar, '<Sidebar.Trigger')).toBe(true);
	});

	it('keeps the sign-out form out of a preview sidebar', () => {
		// A `POST /logout` inside a "live preview" is the most expensive mis-click on the page.
		expect(onlyWhenInteractive(sidebarUser, 'action="/logout"')).toBe(true);
	});

	it('stops a preview provider writing outside its own subtree', () => {
		// A second Sidebar.Provider mounted in a page fought the real one: last mount won
		// `--content-inset-left` on <html> (moving where the app centres dialogs), ⌘B toggled both
		// sidebars, and the rail toggle wrote the shared `sidebar_state` cookie.
		expect(provider).toMatch(/preview\s*=\s*false/);
		expect(provider).toMatch(/if\s*\(preview\)\s*return;/);
		expect(provider).toMatch(/if\s*\(!preview\)\s*\{\s*document\.cookie/);
		expect(provider).toMatch(/if\s*\(!preview\)\s*sidebar\.handleShortcutKeydown/);
		expect(saasShell).toMatch(/preview=\{!interactive\}/);
	});

	it('drops every href in a preview, so it cannot navigate the host app', () => {
		expect(clientShell).toMatch(/linkTo\s*=\s*\(href: string\)\s*=>\s*\(interactive\s*\?/);
		expect(clientShell).not.toMatch(/href="\//);
		expect(sidebar).toMatch(/href=\{interactive \? item\.href : undefined\}/);
		expect(sidebar).toMatch(/href=\{interactive \? '\/' : undefined\}/);
	});
});

describe('client shell stays soft', () => {
	it('keeps the glass bar', () => {
		expect(clientShell).toMatch(/\bglass-bar\b/);
		expect(clientShell).toMatch(/\bglass-canvas\b/);
	});

	it('turns the top bar permanently to glass at the SAME breakpoint the tab bar leaves', () => {
		// Phone chrome is one set: a transparent-until-scrolled header plus the floating tabs.
		// Desktop chrome is the other: an always-glass bar carrying the nav. Both flip at `lg`.
		// The header used to flip at `md`, so a tablet-width window wore a visible bar AND tabs.
		expect(clientShell).toMatch(/'lg:before:glass-bar'/);
		expect(clientShell).not.toMatch(/\bmd:before:glass-bar\b/);
		expect(clientShell).toMatch(/rounded-full lg:hidden"/); // the tab bar
	});

	it('does not opt into the chrome radius cap', () => {
		// The client portal's corners track its own branded `--radius` in full. Capping them here
		// would sharpen the one portal that is meant to stay soft.
		expect(clientShell).not.toMatch(/--radius-chrome/);
	});

	it('shares the tab bar equally and clamps each label (FB-20260917-01)', () => {
		// A long page name widened its tab past its share and the neighbours were cut mid-letter:
		// `flex-1` without `min-w-0` lets content win, and a bare `<span>` never truncates.
		expect(clientShell).toMatch(/'relative z-10 flex min-w-0 flex-1 flex-col items-center/);
		expect(clientShell).toContain('<span class="max-w-full truncate px-1">{item.label}</span>');
	});
});

describe('client shell — immersive page', () => {
	// A conversation is the one sub page whose own header is the better top bar (the person you
	// are talking to is the title, the way back sits beside them). Below `lg` the shell stands
	// down: no bar, no column padding, no reserved scrollbar gutter — each of which drew a strip
	// of canvas around a card meant to be the screen. Desktop is untouched: the bar carries the
	// nav and the page sits in the column.
	it('hides the top bar below lg, and only then', () => {
		expect(clientShell).toMatch(/immersive && 'max-lg:hidden'/);
	});

	it('drops the column padding below lg and keeps it from lg', () => {
		expect(clientShell).toMatch(
			/immersive \? 'lg:px-6 lg:py-10' : 'px-4 py-6 pb-\[var\(--shell-pb\)\] sm:px-6 lg:py-10'/
		);
	});

	it('stops reserving the scrollbar gutter below lg', () => {
		// On a browser with classic scrollbars `stable both-edges` reserves ~15px down BOTH sides
		// of <main> whether or not it scrolls — the "margins" on a page that had none in its CSS.
		expect(clientShell).toMatch(
			/immersive\s*\?\s*'lg:\[scrollbar-gutter:stable_both-edges\]'\s*:\s*'\[scrollbar-gutter:stable_both-edges\]'/
		);
	});

	it('defaults off', () => {
		expect(clientShell).toMatch(/immersive = false/);
	});
});
