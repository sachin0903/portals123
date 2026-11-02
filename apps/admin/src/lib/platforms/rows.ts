/**
 * Shapes for the warehouse query surfaces (the SQL console and the model drawer's Data view).
 *
 * It used to be a great deal more: a seeded PRNG that synthesised plausible values by column name
 * (`P016522` for `firm_id`, names like "A Hartley", GBP amounts, `2026-07-nn` dates) and a toy SQL
 * engine understanding `select <cols|*> from <model> [limit n]`, both serving the fixture datasets.
 * With those gone there is one path — the read-only `lake_reader` attach — and nothing to generate.
 */

/** One cell of a result row. */
export type CellValue = string | number | boolean;

export interface QueryResult {
	columns: string[];
	rows: CellValue[][];
	rowCount: number;
	elapsedMs: number;
	/** The relation the query read, for the result header. */
	relation: string;
	truncated?: boolean;
}

/** A query the warehouse refused, carrying its own message. */
export class QueryError extends Error {}
