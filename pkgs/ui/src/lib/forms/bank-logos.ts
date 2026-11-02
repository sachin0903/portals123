/**
 * Logos for the UK banks a bank details check most often names, served from the hosted bank
 * logotype directory at `content.trustly.com`. Each logo is a round SVG on its own background,
 * addressed by the directory's lowercase bank code, which follows the first four characters of
 * the bank's BIC. A bank is recognised by that BIC prefix when the check returns one, and
 * otherwise by its registered name. Codes the directory does not hold redirect to a generic
 * glyph, so only the banks listed here are ever requested; anything else falls back to initials.
 */
export type BankLogo = { title: string; url: string };

type Entry = { title: string; code: string; bics: string[]; name: RegExp };

const DIRECTORY = 'https://content.trustly.com/logos/banks/GB';

const LOGOS: Entry[] = [
	{ title: 'AIB', code: 'aibk', bics: ['AIBK'], name: /\bALLIED IRISH\b|\bAIB\b/i },
	{ title: 'Alpha FX', code: 'apah', bics: ['APAH'], name: /\bALPHA FX\b/i },
	{ title: 'Arbuthnot Latham', code: 'arbu', bics: ['ARBU'], name: /\bARBUTHNOT\b/i },
	{ title: 'Bank of Ireland UK', code: 'bofi', bics: ['BOFI'], name: /\bBANK OF IRELAND\b/i },
	{ title: 'Bank of Scotland', code: 'bofs', bics: ['BOFS'], name: /^(THE )?BANK OF SCOTLAND\b/i },
	{ title: 'Barclays', code: 'barc', bics: ['BARC'], name: /\bBARCLAYS\b/i },
	{ title: 'C. Hoare & Co.', code: 'hoab', bics: ['HOAB'], name: /\bHOARE\b/i },
	{ title: 'Cashplus', code: 'apsf', bics: ['APSF'], name: /\bCASH ?PLUS\b|\bAPS FINANCIAL\b/i },
	{ title: 'Cater Allen', code: 'cate', bics: ['CATE'], name: /\bCATER ALLEN\b/i },
	{ title: 'Chase', code: 'chas', bics: ['CHAS'], name: /\bCHASE\b|\bJ\.?P\.? ?MORGAN\b/i },
	{ title: 'Chelsea Building Society', code: 'chel', bics: ['CHEL'], name: /\bCHELSEA\b/i },
	{ title: 'Coutts', code: 'cout', bics: ['COUT'], name: /\bCOUTTS\b/i },
	{ title: 'Danske Bank', code: 'daba', bics: ['DABA'], name: /\bDANSKE\b|\bNORTHERN BANK\b/i },
	{ title: 'HSBC', code: 'midl', bics: ['HBUK', 'MIDL'], name: /\bHSBC\b/i },
	{ title: 'Halifax', code: 'hlfx', bics: ['HLFX'], name: /\bHALIFAX\b/i },
	{ title: 'Investec', code: 'ives', bics: ['IVES'], name: /\bINVESTEC\b/i },
	{ title: 'Kleinwort Hambros', code: 'hamb', bics: ['HAMB'], name: /\bKLEINWORT\b|\bHAMBROS\b/i },
	{ title: 'Lloyds Bank', code: 'loyd', bics: ['LOYD'], name: /\bLLOYDS\b/i },
	{ title: 'MBNA', code: 'mbna', bics: ['MBNA'], name: /\bMBNA\b/i },
	{ title: 'Monzo', code: 'monz', bics: ['MONZ'], name: /\bMONZO\b/i },
	{ title: 'NatWest', code: 'nwbk', bics: ['NWBK'], name: /\bNAT(IONAL )?WEST(MINSTER)?\b/i },
	{ title: 'Nationwide', code: 'naia', bics: ['NAIA'], name: /\bNATIONWIDE\b/i },
	{ title: 'Revolut', code: 'revo', bics: ['REVO'], name: /\bREVOLUT\b/i },
	{
		title: 'Royal Bank of Scotland',
		code: 'rbos',
		bics: ['RBOS'],
		name: /\bROYAL BANK OF SCOTLAND\b/i
	},
	{ title: 'Santander UK', code: 'abby', bics: ['ABBY'], name: /\bSANTANDER\b/i },
	{ title: 'Starling Bank', code: 'srlg', bics: ['SRLG'], name: /\bSTARLING\b/i },
	{ title: 'TSB Bank', code: 'tsbs', bics: ['TSBS'], name: /\bTSB\b/i },
	{ title: 'Tesco Bank', code: 'tpfg', bics: ['TPFG'], name: /\bTESCO\b/i },
	{ title: 'The Co-operative Bank', code: 'cpbk', bics: ['CPBK'], name: /\bCO-?OPERATIVE\b/i },
	{ title: 'The Cumberland', code: 'cmbs', bics: ['CMBS'], name: /\bCUMBERLAND\b/i },
	{ title: 'Ulster Bank', code: 'ulsb', bics: ['ULSB'], name: /\bULSTER\b/i },
	{ title: 'Vanquis Bank', code: 'vqis', bics: ['VQIS'], name: /\bVANQUIS\b/i },
	{ title: 'Virgin Money UK', code: 'nrnb', bics: ['NRNB'], name: /\bVIRGIN MONEY\b/i },
	{ title: 'Wise', code: 'trwi', bics: ['TRWI'], name: /^WISE\b|\bTRANSFERWISE\b/i },
	{
		title: 'Yorkshire Building Society',
		code: 'yorb',
		bics: ['YORB'],
		name: /\bYORKSHIRE BUILDING SOCIETY\b/i
	}
];

/** The logo for a bank, from its BIC (such as `BARCGB22`) or its name, or null when there is none. */
export function bankLogo(bic: string | null | undefined, name?: string | null): BankLogo | null {
	const code = (bic ?? '').trim().slice(0, 4).toUpperCase();
	const entry =
		(code && LOGOS.find((logo) => logo.bics.includes(code))) ||
		(name && LOGOS.find((logo) => logo.name.test(name.trim())));
	return entry ? { title: entry.title, url: `${DIRECTORY}/${entry.code}/round.svg` } : null;
}
