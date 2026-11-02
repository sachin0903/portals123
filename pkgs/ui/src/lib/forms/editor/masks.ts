// Lifted verbatim from platform-1 `pkgs/components/src/forms/masked/utils.ts`.
// Pure string transforms, reused across the typed field inputs.

export function formatCurrency(value: string): string {
	const numericValue = value.replace(/[^\d.]/g, '');

	const decimalIndex = numericValue.indexOf('.');
	let cleanedValue = numericValue;
	if (decimalIndex !== -1) {
		cleanedValue =
			numericValue.slice(0, decimalIndex + 1) +
			numericValue.slice(decimalIndex + 1).replace(/\./g, '');
	}

	const parts = cleanedValue.split('.');
	const integerPart = parts[0] || '';
	const decimalPart = parts[1] || '';

	const formattedInteger = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
	const formattedDecimal = decimalPart.slice(0, 2);

	if (cleanedValue.endsWith('.') || formattedDecimal) {
		return `${formattedInteger}.${formattedDecimal}`;
	}

	return formattedInteger;
}

export function formatPercentage(value: string): string {
	const numericValue = value.replace(/[^\d.]/g, '');

	const decimalIndex = numericValue.indexOf('.');
	let cleanedValue = numericValue;
	if (decimalIndex !== -1) {
		cleanedValue =
			numericValue.slice(0, decimalIndex + 1) +
			numericValue.slice(decimalIndex + 1).replace(/\./g, '');
	}

	const parts = cleanedValue.split('.');
	let integerPart = parts[0] || '';
	const decimalPart = parts[1] || '';

	const integerNum = parseInt(integerPart) || 0;
	if (integerNum > 100) {
		integerPart = '100';
	} else if (integerNum === 100 && decimalPart) {
		return '100';
	}

	const formattedDecimal = decimalPart.slice(0, 2);
	if (cleanedValue.endsWith('.') || formattedDecimal) {
		return `${integerPart}.${formattedDecimal}`;
	}

	return integerPart;
}

export function formatDateOfBirth(value: string): string {
	const digits = value.replace(/\D/g, '');

	if (digits.length <= 2) {
		return digits;
	} else if (digits.length <= 4) {
		return `${digits.slice(0, 2)} / ${digits.slice(2)}`;
	} else if (digits.length <= 8) {
		return `${digits.slice(0, 2)} / ${digits.slice(2, 4)} / ${digits.slice(4)}`;
	} else {
		return `${digits.slice(0, 2)} / ${digits.slice(2, 4)} / ${digits.slice(4, 8)}`;
	}
}

function joinPhoneParts(prefix: string, national: string, groups: number[]): string {
	const parts = [prefix];
	let offset = 0;
	for (const size of groups) {
		if (offset >= national.length) break;
		parts.push(national.slice(offset, offset + size));
		offset += size;
	}
	if (offset < national.length) parts.push(national.slice(offset));
	return parts.join(' ');
}

/** Format a UK national number using the recognisable Ofcom groupings. */
function formatUkNumber(national: string): string {
	if (!national) return '+44';
	// Mobiles: 07700 900123 → +44 7700 900123.
	if (national.startsWith('7')) return joinPhoneParts('+44', national, [4, 6]);
	// Two-digit area codes: 020 7946 0958 → +44 20 7946 0958.
	if (/^(20|23|24|28|29)/.test(national)) return joinPhoneParts('+44', national, [2, 4, 4]);
	// Major three-digit area codes: 0113 496 0000 → +44 113 496 0000.
	if (/^(1(?:1[3-8]|21|31|41|51|61|91))/.test(national)) {
		return joinPhoneParts('+44', national, [3, 3, 4]);
	}
	// Most remaining geographic numbers: 01234 567890 → +44 1234 567890.
	return joinPhoneParts('+44', national, [4, 6]);
}

/**
 * Formats a phone number using UK-aware spacing (up to the E.164 15-digit ceiling).
 * Converts leading 0 to +44 for convenience; other countries retain neutral grouping.
 */
export function formatPhoneNumber(value: string): string {
	if (!value) return '';

	const canonical = canonicalPhoneNumber(value);
	if (!canonical) return '';
	const digits = canonical.slice(1);

	if (digits.startsWith('44')) return formatUkNumber(digits.slice(2));
	if (digits.length <= 2) return `+${digits}`;
	if (digits.length <= 6) return `+${digits.slice(0, 2)} ${digits.slice(2)}`;
	if (digits.length <= 10) return `+${digits.slice(0, 2)} ${digits.slice(2, 6)} ${digits.slice(6)}`;
	return `+${digits.slice(0, 2)} ${digits.slice(2, 6)} ${digits.slice(6, 10)} ${digits.slice(10, 15)}`;
}

export function formatBankAccountNumber(value: string): string {
	return value.replace(/\D/g, '').slice(0, 8);
}

export function formatSortCode(value: string): string {
	const digits = value.replace(/\D/g, '').slice(0, 6);

	if (digits.length <= 2) return digits;
	if (digits.length <= 4) return `${digits.slice(0, 2)} - ${digits.slice(2)}`;
	return `${digits.slice(0, 2)} - ${digits.slice(2, 4)} - ${digits.slice(4)}`;
}

/**
 * Block a keystroke that would insert anything other than a digit. `inputmode="numeric"` only
 * hints the mobile keypad — a desktop keyboard still types letters, and a formatter that runs
 * after the fact cannot rewrite the box when the canonical value did not change (`a` and ``
 * both format to empty, so the letter stays). Paste is left to the formatter: `insertFromPaste`
 * is not `insertText`, and a copied `12-34-56` must still land.
 */
export function rejectNonDigitInsert(event: {
	inputType: string;
	data: string | null;
	preventDefault: () => void;
}): void {
	if (event.inputType === 'insertText' && event.data != null && /\D/.test(event.data)) {
		event.preventDefault();
	}
}

/** Listen on the element itself. Putting `oninput` on `<Input>` loses to its `bind:value`. */
/** Characters a phone number can be typed with: digits, a leading `+`, and the separators people
 *  space numbers out with. NOT `rejectNonDigitInsert`, which refuses `+` — an international
 *  number cannot be typed without it. */
const PHONE_CHARS = /^[0-9+\s().-]+$/;

/** Refuse a keystroke that cannot belong to a phone number, at the point it is typed.
 *
 *  Blocking the SAVE is not enough on its own: a field that accepts
 *  `+1111111qasdghd43333…` and only objects afterwards has already let somebody type it, and what
 *  they read back is their own nonsense sitting in a box that looks like it took it. */
export function rejectNonPhoneInsert(event: {
	inputType: string;
	data: string | null;
	preventDefault: () => void;
}): void {
	if (event.inputType === 'insertText' && event.data != null && !PHONE_CHARS.test(event.data)) {
		event.preventDefault();
	}
}

/** What a phone field may hold after any input — including a PASTE, which `beforeinput` filtering
 *  cannot catch character by character.
 *
 *  Keeps a single leading `+`, drops everything that is not a digit or a separator, and stops at
 *  E.164's 15-digit ceiling so a pasted essay cannot sit in the box at all. */
export function sanitisePhoneInput(value: string): string {
	const plus = value.trimStart().startsWith('+');
	let digits = 0;
	let out = '';
	for (const ch of value) {
		if (/[0-9]/.test(ch)) {
			if (digits >= 15) continue;
			digits += 1;
			out += ch;
		} else if (/[\s().-]/.test(ch)) {
			out += ch;
		}
	}
	return (plus ? '+' : '') + out;
}

/** The phone twin of `attachDigitMask`, and it does deliberately LESS than its sibling.
 *
 *  It has to be a separate function rather than an option: a phone number is not a digit string —
 *  it is typed with a leading `+` and spaced out with separators, so `rejectNonDigitInsert` would
 *  refuse the one character an international number cannot be written without.
 *
 *  **It does not reformat while you type.** The digit masks rewrite the box on every keystroke, and
 *  doing that here was actively hostile: typing `1234` canonicalised to `+1234` and redisplayed as
 *  `+12 34`, so the field invented a country code from the first two digits somebody had got
 *  halfway through entering. It also destroyed the only evidence `phoneIssue` needs — once bare
 *  digits have been given a `+`, "this is neither a UK nor an international number" can no longer
 *  be said. So the box keeps what was typed, minus what cannot belong in a phone number at all.
 *
 *  Both remaining halves are load-bearing. `beforeinput` refuses a typed letter at the keystroke,
 *  because a formatter running afterwards cannot clear the box when the value did not change — `a`
 *  and `` both reduce to empty, so nothing re-renders and the letter stays on screen.
 *  `sanitisePhoneInput` then catches what `beforeinput` cannot see character by character: a paste,
 *  and the 16th digit of a number already at E.164's ceiling.
 */
export function attachPhoneMask(
	node: HTMLInputElement,
	options: { onchange: (value: string) => void }
): () => void {
	const onBefore = (event: Event) => {
		if (event instanceof InputEvent) rejectNonPhoneInsert(event);
	};
	const onIn = () => {
		const cleaned = sanitisePhoneInput(node.value);
		// Only touch the DOM when something was actually removed. Writing an identical string back
		// on every keystroke moves the caret to the end, which makes correcting a digit in the
		// middle of a number impossible.
		if (node.value !== cleaned) {
			const atEnd = node.selectionStart === node.value.length;
			node.value = cleaned;
			if (atEnd) node.setSelectionRange(cleaned.length, cleaned.length);
		}
		options.onchange(cleaned);
	};
	node.addEventListener('beforeinput', onBefore);
	node.addEventListener('input', onIn);
	return () => {
		node.removeEventListener('beforeinput', onBefore);
		node.removeEventListener('input', onIn);
	};
}

export function attachDigitMask(
	node: HTMLInputElement,
	options: {
		canonical: (value: string) => string;
		format: (value: string) => string;
		onchange: (value: string) => void;
	}
): () => void {
	const onBefore = (event: Event) => {
		if (event instanceof InputEvent) rejectNonDigitInsert(event);
	};
	const onIn = () => {
		const next = options.canonical(node.value);
		node.value = options.format(next);
		options.onchange(next);
	};
	node.addEventListener('beforeinput', onBefore);
	node.addEventListener('input', onIn);
	return () => {
		node.removeEventListener('beforeinput', onBefore);
		node.removeEventListener('input', onIn);
	};
}

/**
 * Canonicalise a phone value to `+<digits>` (≤15) — the stored form.
 *
 * A UK number written with the trunk prefix (`07812 345678`, `020 7946 0958`) is `+44` and the
 * ten digits after the `0`. `0044…` is that same number dialled with the international prefix,
 * and `+440…` is `+44` with the trunk `0` still typed. A leading `0` that is not `00` is mapped
 * to `+44` so a half-typed `078` compares with a stored `+4478`; the server only *stores* that
 * form once the number is a full 11 digits.
 */
export function canonicalPhoneNumber(value: string): string {
	let cleaned = value.replace(/[^\d+]/g, '');
	if (cleaned.startsWith('00')) {
		cleaned = '+' + cleaned.slice(2);
	} else if (!cleaned.startsWith('+') && cleaned.startsWith('0')) {
		cleaned = '+44' + cleaned.slice(1);
	}
	if (!cleaned.startsWith('+')) {
		const digits = cleaned.replace(/\D/g, '');
		cleaned = digits ? `+${digits}` : '';
	}
	let digits = cleaned.slice(1).replace(/\D/g, '').slice(0, 15);
	// +44 with the trunk 0 still in it: +4407812… is +447812…
	if (digits.startsWith('440') && digits.length === 13) {
		digits = '44' + digits.slice(3);
	}
	return digits ? `+${digits}` : '';
}

/**
 * What is WRONG with a phone number, in words, or null when there is nothing to say.
 *
 * Mirrors `app/contacts.py::validate_contact_phone` — the server is the gate, this is so the
 * person hears about it at the field rather than after a save. A UK number is checked because it
 * is the one whose length we know: 11 digits nationally, which is 10 after the `+44` that
 * {@link canonicalPhoneNumber} turns a leading `0` into. Everything else is measured only against
 * E.164's own bounds, since an overseas number's length is not ours to assert.
 *
 * Empty is not an issue: these fields are optional, and clearing one is a legitimate edit.
 */
export function phoneIssue(value: string): string | null {
	const typed = value.trim();
	if (!typed) return null;
	// A number has to be identifiably ONE THING: a UK number (leading 0, or +44) or an explicitly
	// international one (+ and its country code). Bare digits are neither, and treating them as
	// international is what let `1111111` save happily — seven ones clear E.164's floor, so the
	// field accepted a number nobody could ring and read it back as "+11 1111 1".
	const compact = typed.replace(/[\s().-]/g, '');
	if (!/^\+?\d+$/.test(compact)) {
		return 'Enter a phone number using digits only.';
	}
	if (!compact.startsWith('+') && !compact.startsWith('0')) {
		return 'Enter a UK number starting 0, or an international number with its country code (+).';
	}
	const digits = canonicalPhoneNumber(compact).slice(1);
	if (digits.startsWith('44')) {
		return digits.length - 2 === 10
			? null
			: 'A UK phone number is 11 digits — for example 07700 900123.';
	}
	return digits.length >= 7 && digits.length <= 15
		? null
		: 'Enter a phone number with its country code, for example +353 1 234 5678.';
}

/** The shape an email address must have: something, `@`, a domain, a dot and a two-letter-plus
 *  ending. The same rule the platform and admin server actions apply (`EMAIL_RE` in the clients
 *  routes), so the box never passes an address the action then refuses. */
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

/** The sentence `EmailInput` shows under the box, or null. Null while empty — an unanswered
 *  optional field is not an error. Named as it is typed, like `phoneIssue`, not only on save. */
export function emailIssue(value: string): string | null {
	const typed = value.trim();
	if (!typed) return null;
	return EMAIL_SHAPE.test(typed) ? null : 'Enter an email address, for example name@example.com.';
}

/** Canonicalise a sort code to `12-34-56` when complete, else the partial digits. */
export function canonicalSortCode(value: string): string {
	const digits = value.replace(/\D/g, '').slice(0, 6);
	return digits.length === 6
		? `${digits.slice(0, 2)}-${digits.slice(2, 4)}-${digits.slice(4, 6)}`
		: digits;
}
