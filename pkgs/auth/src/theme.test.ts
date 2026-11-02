import { describe, expect, it } from 'vitest';
import {
	CHOICE_ATTR,
	COLOUR_SCHEME_HINT,
	DARK_CLASS,
	MODE_ATTR,
	THEME_CHOICES,
	THEME_COOKIE,
	effectiveChoice,
	parseTenantMode,
	parseThemeChoice,
	prefersDarkFromHint,
	resolveScheme,
	themeAttributes,
	themeChoiceFromCookie,
	themeCookie,
	themeHead,
	type ThemeChoice,
	type TenantMode
} from './theme';

describe('parseThemeChoice', () => {
	it('accepts the three known values', () => {
		expect(parseThemeChoice('light')).toBe('light');
		expect(parseThemeChoice('dark')).toBe('dark');
		expect(parseThemeChoice('system')).toBe('system');
	});

	it('rejects anything else', () => {
		for (const raw of ['', 'DARK', 'auto', 'sepia', null, undefined])
			expect(parseThemeChoice(raw)).toBeNull();
	});
});

describe('parseTenantMode', () => {
	it('accepts the branding modes', () => {
		expect(parseTenantMode('auto')).toBe('auto');
		expect(parseTenantMode('light')).toBe('light');
		expect(parseTenantMode('dark')).toBe('dark');
	});

	it('rejects anything else', () => {
		for (const raw of ['', 'system', 'AUTO', null, undefined])
			expect(parseTenantMode(raw)).toBeNull();
	});
});

describe('themeChoiceFromCookie', () => {
	it('finds the cookie among others', () => {
		expect(themeChoiceFromCookie(`wos-session=abc; ${THEME_COOKIE}=dark; other=1`)).toBe('dark');
	});

	it('tolerates whitespace and a single-cookie header', () => {
		expect(themeChoiceFromCookie(`  ${THEME_COOKIE} = light `)).toBe('light');
	});

	it('is null when absent, empty or unrecognised', () => {
		expect(themeChoiceFromCookie(null)).toBeNull();
		expect(themeChoiceFromCookie('')).toBeNull();
		expect(themeChoiceFromCookie('wos-session=abc')).toBeNull();
		expect(themeChoiceFromCookie(`${THEME_COOKIE}=sepia`)).toBeNull();
	});

	it('does not match a cookie whose name merely ends with the theme cookie', () => {
		expect(themeChoiceFromCookie(`not-${THEME_COOKIE}=dark`)).toBeNull();
	});

	it('round-trips what themeCookie writes', () => {
		for (const choice of THEME_CHOICES)
			expect(themeChoiceFromCookie(themeCookie(choice).split(';')[0])).toBe(choice);
	});
});

describe('effectiveChoice', () => {
	it('prefers the user choice over the tenant mode', () => {
		expect(effectiveChoice({ choice: 'light', mode: 'dark' })).toBe('light');
		expect(effectiveChoice({ choice: 'dark', mode: 'light' })).toBe('dark');
	});

	it('treats an explicit `system` choice as beating the tenant mode', () => {
		expect(effectiveChoice({ choice: 'system', mode: 'dark' })).toBe('system');
	});

	it('falls back to the tenant mode when no choice was made', () => {
		expect(effectiveChoice({ mode: 'dark' })).toBe('dark');
		expect(effectiveChoice({ mode: 'light' })).toBe('light');
	});

	it('falls back to system for `auto`, no mode, or junk', () => {
		expect(effectiveChoice({ mode: 'auto' })).toBe('system');
		expect(effectiveChoice({})).toBe('system');
		expect(effectiveChoice({ choice: null, mode: null })).toBe('system');
	});
});

describe('resolveScheme', () => {
	// choice > tenant mode > OS. `null` = "the client must decide" (never silently light).
	const cases: {
		choice?: ThemeChoice | null;
		mode?: TenantMode | null;
		prefersDark?: boolean | null;
		expected: 'light' | 'dark' | null;
	}[] = [
		// 1. explicit choice wins over everything
		{ choice: 'dark', mode: 'light', prefersDark: false, expected: 'dark' },
		{ choice: 'light', mode: 'dark', prefersDark: true, expected: 'light' },
		{ choice: 'system', mode: 'dark', prefersDark: false, expected: 'light' },
		{ choice: 'system', mode: 'light', prefersDark: true, expected: 'dark' },
		// 2. tenant mode wins over the OS when no choice was made
		{ mode: 'dark', prefersDark: false, expected: 'dark' },
		{ mode: 'light', prefersDark: true, expected: 'light' },
		// 3. the OS decides when neither is set
		{ mode: 'auto', prefersDark: true, expected: 'dark' },
		{ mode: 'auto', prefersDark: false, expected: 'light' },
		{ prefersDark: true, expected: 'dark' },
		// unresolvable: system-ish AND the OS preference is unknown (server, no client hint)
		{ expected: null },
		{ mode: 'auto', expected: null },
		{ choice: 'system', mode: 'dark', expected: null },
		{ mode: 'auto', prefersDark: null, expected: null }
	];

	for (const { expected, ...input } of cases)
		it(`${JSON.stringify(input)} → ${expected}`, () => {
			expect(resolveScheme(input)).toBe(expected);
		});

	it('is never null once a scheme is forced, even with no OS signal', () => {
		expect(resolveScheme({ choice: 'dark' })).toBe('dark');
		expect(resolveScheme({ mode: 'light' })).toBe('light');
	});
});

describe('prefersDarkFromHint', () => {
	it('reads the client hint', () => {
		expect(prefersDarkFromHint('dark')).toBe(true);
		expect(prefersDarkFromHint('light')).toBe(false);
		// Some intermediaries quote structured-header sf-strings.
		expect(prefersDarkFromHint('"dark"')).toBe(true);
		expect(prefersDarkFromHint(' Light ')).toBe(false);
	});

	it('is null when absent or unrecognised', () => {
		expect(prefersDarkFromHint(null)).toBeNull();
		expect(prefersDarkFromHint(undefined)).toBeNull();
		expect(prefersDarkFromHint('')).toBeNull();
		expect(prefersDarkFromHint('no-preference')).toBeNull();
	});

	it('names the header in its canonical form (used for both Accept-CH and the lookup)', () => {
		expect(COLOUR_SCHEME_HINT).toBe('Sec-CH-Prefers-Color-Scheme');
	});
});

describe('themeCookie', () => {
	it('writes a site-wide, long-lived, lax cookie', () => {
		const cookie = themeCookie('dark');
		expect(cookie).toContain(`${THEME_COOKIE}=dark`);
		expect(cookie).toContain('path=/');
		expect(cookie).toContain('samesite=lax');
		expect(cookie).toMatch(/max-age=\d+/);
	});

	it('adds `secure` only when asked (localhost dev is http)', () => {
		expect(themeCookie('light', { secure: true })).toContain('secure');
		expect(themeCookie('light')).not.toContain('secure');
	});

	it('never writes an unknown value', () => {
		expect(themeCookie('sepia' as ThemeChoice)).toContain(`${THEME_COOKIE}=system`);
	});
});

describe('themeAttributes', () => {
	it('emits the dark class only when the scheme resolves to dark', () => {
		expect(themeAttributes({ choice: 'dark' })).toContain(`class="${DARK_CLASS}"`);
		expect(themeAttributes({ choice: 'light' })).not.toContain('class=');
		// Unresolvable ⇒ no class at all; the head script settles it before paint.
		expect(themeAttributes({ mode: 'auto' })).not.toContain('class=');
	});

	it('carries the explicit choice, and omits the attribute when there is none', () => {
		expect(themeAttributes({ choice: 'system', mode: 'dark' })).toContain(
			`${CHOICE_ATTR}="system"`
		);
		expect(themeAttributes({ mode: 'dark' })).not.toContain(CHOICE_ATTR);
	});

	it('always carries the tenant mode, defaulting to auto', () => {
		expect(themeAttributes({ mode: 'dark' })).toContain(`${MODE_ATTR}="dark"`);
		expect(themeAttributes({})).toContain(`${MODE_ATTR}="auto"`);
		expect(themeAttributes({ mode: 'nonsense' as TenantMode })).toContain(`${MODE_ATTR}="auto"`);
	});

	it('produces nothing that could break out of the html tag', () => {
		const attrs = themeAttributes({ choice: 'dark', mode: 'light' });
		expect(attrs).toBe(`class="dark" ${CHOICE_ATTR}="dark" ${MODE_ATTR}="light"`);
		expect(attrs).not.toMatch(/[<>]/);
	});
});

describe('themeHead', () => {
	const head = themeHead();

	it('is a single inline script that cannot close the tag early', () => {
		expect(head.startsWith('<script>')).toBe(true);
		expect(head.endsWith('</script>')).toBe(true);
		expect(head.slice(8, -9)).not.toContain('</script');
	});

	it('reads the same attributes themeAttributes writes', () => {
		expect(head).toContain(CHOICE_ATTR);
		expect(head).toContain(MODE_ATTR);
		expect(head).toContain(DARK_CLASS);
	});

	it('subscribes to OS changes rather than reading the preference once', () => {
		expect(head).toContain('prefers-color-scheme: dark');
		expect(head).toContain('addEventListener("change"');
	});

	/** The script is the runtime twin of `resolveScheme`; run it against a fake DOM and assert the
	 * two agree on every combination, so a change to one that misses the other fails here. */
	it('resolves exactly like resolveScheme', () => {
		const source = head.slice('<script>'.length, -'</script>'.length);
		for (const choice of [null, ...THEME_CHOICES] as (ThemeChoice | null)[])
			for (const mode of [null, 'auto', 'light', 'dark'] as (TenantMode | null)[])
				for (const prefersDark of [true, false]) {
					const classes = new Set<string>();
					const root = {
						getAttribute: (name: string) =>
							name === CHOICE_ATTR ? choice : name === MODE_ATTR ? mode : null,
						classList: {
							toggle: (name: string, on: boolean) => (on ? classes.add(name) : classes.delete(name))
						}
					};
					const scope = {
						document: { documentElement: root },
						window: { matchMedia: () => ({ matches: prefersDark, addEventListener: () => {} }) }
					};
					new Function('document', 'window', source)(scope.document, scope.window);
					expect(classes.has(DARK_CLASS), JSON.stringify({ choice, mode, prefersDark })).toBe(
						resolveScheme({ choice, mode, prefersDark }) === 'dark'
					);
				}
	});
});
