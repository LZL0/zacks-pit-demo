// Thin fetch client over the generated Zod schemas.
// - Requests are parsed with the generated query schemas before anything is sent, so a
//   malformed ticker, period or date throws a ZodError locally, under the server's rules.
// - Responses are parsed with the generated response schemas, so a server/schema
//   mismatch fails at the boundary instead of deep in user code.
import type { z } from "zod";
import {
  AdjustmentExplanationSchema,
  ConsensusQuerySchema,
  ConsensusResultSchema,
  ConsensusSeriesSchema,
  CorporateActionListSchema,
  CorporateActionsQuerySchema,
  ErrorResponseSchema,
  ExplainQuerySchema,
  RevisionSignalSchema,
  RevisionsQuerySchema,
  SeriesQuerySchema,
  UniverseQuerySchema,
  UniverseSchema,
  type AdjustmentExplanation,
  type ConsensusResult,
  type ConsensusSeries,
  type CorporateActionList,
  type ErrorResponse,
  type RevisionSignal,
  type Universe,
} from "./schemas.js";

export type Basis = "adjusted" | "unadjusted";

/** `error` is the typed body when the server sent one; otherwise null and `body` has the raw text. */
export class PitApiError extends Error {
  constructor(
    readonly status: number,
    readonly error: ErrorResponse | null,
    readonly body = "",
  ) {
    super(error ? `${status} ${error.error}: ${error.message}` : `${status} ${body.slice(0, 200)}`);
    this.name = "PitApiError";
  }
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

export class PitClient {
  constructor(
    private readonly baseUrl = "http://127.0.0.1:8000",
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  private async get<S extends z.ZodTypeAny>(path: string, schema: S, query: Record<string, unknown>): Promise<z.infer<S>> {
    const url = new URL(path, this.baseUrl);
    for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    const res = await this.fetchImpl(url);
    const text = await res.text();
    if (!res.ok) {
      const typed = ErrorResponseSchema.safeParse(parseJson(text));
      throw new PitApiError(res.status, typed.success ? typed.data : null, text);
    }
    return schema.parse(parseJson(text));
  }

  /** Point-in-time consensus. `asOf` as YYYY-MM-DD means end of that day, UTC. */
  consensus(ticker: string, period: string, asOf: string, basis: Basis = "adjusted", knownAsOf?: string): Promise<ConsensusResult> {
    const query = ConsensusQuerySchema.parse({ ticker, period, as_of: asOf, basis, known_as_of: knownAsOf ?? null });
    return this.get("/consensus", ConsensusResultSchema, query);
  }

  consensusSeries(ticker: string, period: string, start: string, end: string, basis: Basis = "adjusted", knownAsOf?: string): Promise<ConsensusSeries> {
    const query = SeriesQuerySchema.parse({ ticker, period, start, end, basis, known_as_of: knownAsOf ?? null });
    return this.get("/consensus/series", ConsensusSeriesSchema, query);
  }

  corporateActions(ticker: string): Promise<CorporateActionList> {
    return this.get("/corporate-actions", CorporateActionListSchema, CorporateActionsQuerySchema.parse({ ticker }));
  }

  explain(ticker: string, date: string, period = "FY1"): Promise<AdjustmentExplanation> {
    return this.get("/explain", AdjustmentExplanationSchema, ExplainQuerySchema.parse({ ticker, date, period }));
  }

  revisions(ticker: string, period: string, asOf: string, lookbackDays = 30): Promise<RevisionSignal> {
    const query = RevisionsQuerySchema.parse({ ticker, period, as_of: asOf, lookback_days: lookbackDays });
    return this.get("/revisions", RevisionSignalSchema, query);
  }

  /** Securities listed on `date` (delisted names drop out). */
  universe(date: string): Promise<Universe> {
    return this.get("/universe", UniverseSchema, UniverseQuerySchema.parse({ date }));
  }
}
