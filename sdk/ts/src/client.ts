// Thin fetch client. Every response is parsed with the generated Zod schemas, so a
// server/schema mismatch fails loudly at the boundary instead of deep in user code.
import type { z } from "zod";
import {
  AdjustmentExplanationSchema,
  ConsensusResultSchema,
  ConsensusSeriesSchema,
  CorporateActionListSchema,
  ErrorResponseSchema,
  RevisionSignalSchema,
  type AdjustmentExplanation,
  type ConsensusResult,
  type ConsensusSeries,
  type CorporateActionList,
  type ErrorResponse,
  type RevisionSignal,
} from "./schemas.js";

export type Basis = "adjusted" | "unadjusted";
type Params = Record<string, string | number | undefined>;

export class PitApiError extends Error {
  constructor(
    readonly status: number,
    readonly error: ErrorResponse,
  ) {
    super(`${status} ${error.error}: ${error.message}`);
    this.name = "PitApiError";
  }
}

export class PitClient {
  constructor(
    private readonly baseUrl = "http://127.0.0.1:8000",
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  private async get<S extends z.ZodTypeAny>(path: string, schema: S, params: Params): Promise<z.infer<S>> {
    const url = new URL(path, this.baseUrl);
    for (const [k, v] of Object.entries(params)) if (v !== undefined) url.searchParams.set(k, String(v));
    const res = await this.fetchImpl(url);
    const body: unknown = await res.json();
    if (!res.ok) throw new PitApiError(res.status, ErrorResponseSchema.parse(body));
    return schema.parse(body);
  }

  /** Point-in-time consensus. `asOf` as YYYY-MM-DD means end of that day, UTC. */
  consensus(ticker: string, period: string, asOf: string, basis: Basis = "adjusted", knownAsOf?: string): Promise<ConsensusResult> {
    return this.get("/consensus", ConsensusResultSchema, { ticker, period, as_of: asOf, basis, known_as_of: knownAsOf });
  }

  consensusSeries(ticker: string, period: string, start: string, end: string, basis: Basis = "adjusted"): Promise<ConsensusSeries> {
    return this.get("/consensus/series", ConsensusSeriesSchema, { ticker, period, start, end, basis });
  }

  corporateActions(ticker: string): Promise<CorporateActionList> {
    return this.get("/corporate-actions", CorporateActionListSchema, { ticker });
  }

  explain(ticker: string, date: string, period = "FY1"): Promise<AdjustmentExplanation> {
    return this.get("/explain", AdjustmentExplanationSchema, { ticker, date, period });
  }

  revisions(ticker: string, period: string, asOf: string, lookbackDays = 30): Promise<RevisionSignal> {
    return this.get("/revisions", RevisionSignalSchema, { ticker, period, as_of: asOf, lookback_days: lookbackDays });
  }
}
