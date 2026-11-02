export interface LookthroughPosition {
	ordinal: number;
	isin: string | null;
	sedol: string | null;
	name: string;
	long_name: string | null;
	currency: string | null;
	asset_group: string | null;
	asset_class: string | null;
	country_code: string | null;
	country_name: string | null;
	maturity_date: string | null;
	quantity: number | null;
	price: number | null;
	prior_price: number | null;
	movement_percent: number | null;
	book_cost: number | null;
	market_value: number;
	weight: number;
	indirect_value: number;
	gain_loss: number | null;
	gain_loss_percent: number | null;
}

export interface LookthroughBreakdown {
	label: string;
	value: number;
}

export interface FundLookthroughData {
	fund: { uid: string; name: string; source: string | null; fund_code: string | null };
	share_class: { isin: string | null; sedol: string | null; name: string | null };
	share_classes: {
		class_code: string;
		name: string;
		isin: string;
		sedol: string | null;
	}[];
	snapshot: {
		uid: string;
		as_of_date: string;
		coverage: number;
		residual: number;
		position_count: number;
	};
	available_dates: string[];
	holding: {
		value: number;
		currency: string;
		account_uid: string;
		account_name: string | null;
	} | null;
	positions: LookthroughPosition[];
	nav_history: {
		class_code: string | null;
		class_name: string | null;
		change: number | null;
		points: {
			date: string;
			class_code: string;
			class_name: string | null;
			nav: number;
			units_outstanding: number | null;
			fund_value: number | null;
		}[];
	};
	cash: {
		balances: {
			currency: string;
			name: string;
			base_value: number;
			weight: number;
		}[];
		net_weight: number;
		other_residual: number;
	};
	maturity_profile: LookthroughBreakdown[];
	currency_contracts: {
		share_class_code: string | null;
		name: string;
		currency: string | null;
		delivery_date: string | null;
		bought_sold: string | null;
		amount: number | null;
		current_value: number | null;
		unrealised_gain_loss: number | null;
		nav_impact: number | null;
		long_short: string | null;
	}[];
	breakdowns: {
		country: LookthroughBreakdown[];
		currency: LookthroughBreakdown[];
		asset_class: LookthroughBreakdown[];
	};
}

export interface LookthroughFundSummary {
	uid: string;
	name: string;
	fund_code: string | null;
	share_class_count: number;
	isins: string[];
	latest_as_of: string | null;
	position_count: number;
	coverage: number | null;
	imported_at: string | null;
}

export interface LookthroughImport {
	uid: string;
	fund_uid: string;
	fund_name: string;
	filename: string;
	as_of_date: string;
	position_count: number;
	share_class_count: number;
	imported_at: string;
	supersedes_uid: string | null;
}

export interface LookthroughAdminData {
	funds: LookthroughFundSummary[];
	imports: LookthroughImport[];
}

export interface LookthroughImportResult {
	filename: string;
	status: 'imported' | 'corrected' | 'enriched' | 'unchanged' | 'rejected';
	error?: string;
	fund_uid?: string | null;
	fund_name?: string | null;
	as_of_date?: string | null;
	share_class_count?: number;
	position_count?: number;
}
