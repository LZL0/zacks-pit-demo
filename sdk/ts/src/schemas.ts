// Generated from sdk/schema.json by `npm run gen`. Do not edit by hand.
import { z } from "zod";

export const ContributorSchema = z.object({ "basis_date": z.string().date().describe("Date whose share count the raw value is on."), "basis_inferred": z.boolean().describe("True when the basis is a guess: a date fallback, or inferred from a prior estimate whose own basis was a guess."), "basis_source": z.enum(["flagged","ratio_inferred","date_fallback","entry_date"]).describe("flagged: source supplied the basis; ratio_inferred: matched the broker's prior estimate divided by the split ratio; date_fallback: guessed from entry date near a split; entry_date: no split nearby, so the entry date is the basis."), "broker_id": z.string(), "entered_at": z.string().datetime({ offset: true }), "estimate_id": z.string(), "factor": z.number().describe("Cumulative share factor from basis_date to the target."), "raw_value": z.number().describe("Value as published, on its own share basis."), "value": z.number().describe("raw_value / factor: the value on the target basis.") }).strict().describe("One estimate that went into the consensus, with its basis decision.");
export type Contributor = z.infer<typeof ContributorSchema>;

export const CorporateActionSchema = z.object({ "action_type": z.enum(["SPLIT","REVERSE_SPLIT","SPINOFF","DELISTING"]), "ex_date": z.string().date().describe("First trading day on the new basis."), "known_at": z.string().datetime({ offset: true }).describe("When the action became public (UTC)."), "notes": z.union([z.string(), z.null()]).default(null), "ratio": z.number().describe("New shares per old share (10.0 for 10-for-1, 0.125 for 1-for-8). 1.0 when the share count does not change."), "record_date": z.union([z.string().date(), z.null()]), "security_id": z.string(), "source_url": z.string().describe("Primary source (SEC filing or regulator release)."), "terms": z.string(), "ticker": z.string().describe("Ticker at the time of the action.") }).strict();
export type CorporateAction = z.infer<typeof CorporateActionSchema>;

export const ExclusionSchema = z.object({ "broker_id": z.string(), "detail": z.string(), "entered_at": z.string().datetime({ offset: true }), "estimate_id": z.string(), "raw_value": z.number(), "reason": z.enum(["stale","not_reconfirmed_after_report","pre_spinoff_perimeter","outlier"]) }).strict();
export type Exclusion = z.infer<typeof ExclusionSchema>;

export const SecurityRefSchema = z.object({ "cik": z.string().describe("SEC Central Index Key, zero-padded to 10 digits."), "name": z.string(), "security_id": z.string().describe("Permanent identifier, e.g. SEC-0001. Never reused."), "ticker": z.union([z.string(), z.null()]).describe("Ticker in use on the as-of date, if any.") }).strict();
export type SecurityRef = z.infer<typeof SecurityRefSchema>;

export const VersionsSchema = z.object({ "estimates": z.number().int(), "factors": z.number().int(), "fn": z.string(), "reports": z.number().int() }).strict();
export type Versions = z.infer<typeof VersionsSchema>;

export const ConsensusResultSchema = z.object({ "as_of": z.string().datetime({ offset: true }).describe("Estimates known at this instant (UTC)."), "basis": z.enum(["adjusted","unadjusted"]), "contributors": z.array(ContributorSchema), "contributors_total": z.number().int(), "corporate_actions_applied": z.array(CorporateActionSchema), "count": z.number().int(), "excluded": z.array(ExclusionSchema), "excluded_total": z.number().int(), "fiscal_period": z.string().describe("Fixed fiscal-year label, e.g. FY2025."), "high": z.union([z.number(), z.null()]), "insufficient": z.boolean().describe("True when fewer than 3 estimates qualify."), "known_as_of": z.union([z.string().datetime({ offset: true }), z.null()]).describe("Corporate-action knowledge cutoff. None means everything known today."), "low": z.union([z.number(), z.null()]), "mean": z.union([z.number(), z.null()]), "median": z.union([z.number(), z.null()]), "method": z.string(), "metric": z.literal("EPS").default("EPS"), "requested_period": z.string().describe("Label as requested (may be FY1/FY2)."), "security": SecurityRefSchema, "stdev": z.union([z.number(), z.null()]).describe("Sample standard deviation (n-1)."), "synthetic_estimates": z.boolean().describe("Analyst estimates in this demo are synthetic; actuals and corporate actions are real.").default(true), "target_basis": z.string().describe("Plain-language description of the share basis."), "target_basis_date": z.union([z.string().date(), z.null()]).describe("Share basis of the returned values. None means the latest known basis."), "versions": VersionsSchema, "warnings": z.array(z.string()) }).strict().describe("Point-in-time EPS consensus for one security and fiscal period.");
export type ConsensusResult = z.infer<typeof ConsensusResultSchema>;

export const AdjustmentExplanationSchema = z.object({ "as_of": z.string().datetime({ offset: true }), "consensus_adjusted": ConsensusResultSchema, "fiscal_period": z.string(), "naive_mean": z.union([z.number(), z.null()]).describe("Average of raw published values with no basis handling, for contrast."), "narrative": z.array(z.string()), "security": SecurityRefSchema }).strict();
export type AdjustmentExplanation = z.infer<typeof AdjustmentExplanationSchema>;

export const CacheInfoSchema = z.object({ "hits": z.number().int(), "misses": z.number().int(), "size": z.number().int() }).strict();
export type CacheInfo = z.infer<typeof CacheInfoSchema>;

export const ConsensusPointSchema = z.object({ "as_of": z.string().date(), "count": z.number().int(), "insufficient": z.boolean(), "mean": z.union([z.number(), z.null()]), "median": z.union([z.number(), z.null()]) }).strict();
export type ConsensusPoint = z.infer<typeof ConsensusPointSchema>;

export const InstantParamSchema = z.string().regex(new RegExp("^[0-9]{4}-[0-9]{2}-[0-9]{2}([T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\\.[0-9]+)?)?(Z|[+-][0-9]{2}:?[0-9]{2})?)?$")).describe("ISO date (read as the end of that day, UTC) or ISO datetime (UTC unless an offset is given).");
export type InstantParam = z.infer<typeof InstantParamSchema>;

export const PeriodParamSchema = z.string().regex(new RegExp("^\\s*[Ff][Yy]([0-9]{4}|[12])\\s*$")).describe("Fixed fiscal-year label (FY2025, preferred) or relative FY1/FY2.");
export type PeriodParam = z.infer<typeof PeriodParamSchema>;

export const TickerParamSchema = z.string().regex(new RegExp("^\\s*([A-Za-z][A-Za-z0-9.\\-]{0,9}|[Ss][Ee][Cc]-[0-9]{4})\\s*$")).describe("Ticker as of the query date (e.g. NVDA, SIVB), or a permanent security id (e.g. SEC-0005).");
export type TickerParam = z.infer<typeof TickerParamSchema>;

export const ConsensusQuerySchema = z.object({ "as_of": InstantParamSchema, "basis": z.enum(["adjusted","unadjusted"]).default("adjusted"), "known_as_of": z.union([InstantParamSchema, z.null()]).describe("Ignore corporate actions announced after this instant.").default(null), "period": PeriodParamSchema, "ticker": TickerParamSchema }).strict();
export type ConsensusQuery = z.infer<typeof ConsensusQuerySchema>;

export const ConsensusSeriesSchema = z.object({ "basis": z.enum(["adjusted","unadjusted"]), "fiscal_period": z.string(), "known_as_of": z.union([z.string().datetime({ offset: true }), z.null()]), "points": z.array(ConsensusPointSchema), "security": SecurityRefSchema }).strict();
export type ConsensusSeries = z.infer<typeof ConsensusSeriesSchema>;

export const CorporateActionListSchema = z.object({ "actions": z.array(CorporateActionSchema), "security": SecurityRefSchema }).strict();
export type CorporateActionList = z.infer<typeof CorporateActionListSchema>;

export const CorporateActionsQuerySchema = z.object({ "ticker": TickerParamSchema }).strict();
export type CorporateActionsQuery = z.infer<typeof CorporateActionsQuerySchema>;

export const ErrorResponseSchema = z.object({ "error": z.enum(["NotFound","InsufficientEstimates","InvalidRequest"]), "hint": z.union([z.string(), z.null()]).default(null), "message": z.string() }).strict();
export type ErrorResponse = z.infer<typeof ErrorResponseSchema>;

export const ExplainQuerySchema = z.object({ "date": z.string().date(), "period": PeriodParamSchema, "ticker": TickerParamSchema }).strict();
export type ExplainQuery = z.infer<typeof ExplainQuerySchema>;

export const FixedPeriodParamSchema = z.string().regex(new RegExp("^\\s*[Ff][Yy][0-9]{4}\\s*$")).describe("Fixed fiscal-year label, e.g. FY2025.");
export type FixedPeriodParam = z.infer<typeof FixedPeriodParamSchema>;

export const HealthSchema = z.object({ "api_version": z.string().describe("Dated API version, e.g. 2026-10."), "cache": CacheInfoSchema, "ok": z.boolean() }).strict();
export type Health = z.infer<typeof HealthSchema>;

export const RevisionSignalSchema = z.object({ "as_of": z.string().datetime({ offset: true }), "brokers_compared": z.number().int(), "down": z.number().int(), "fiscal_period": z.string(), "lookback_days": z.number().int(), "mean_pct_change": z.union([z.number(), z.null()]), "security": SecurityRefSchema, "unchanged": z.number().int(), "up": z.number().int() }).strict().describe("Per-broker revisions over a lookback window, comparing like with like.");
export type RevisionSignal = z.infer<typeof RevisionSignalSchema>;

export const RevisionsQuerySchema = z.object({ "as_of": InstantParamSchema, "lookback_days": z.number().int().gte(1).lte(365).default(30), "period": PeriodParamSchema, "ticker": TickerParamSchema }).strict();
export type RevisionsQuery = z.infer<typeof RevisionsQuerySchema>;

export const SeriesQuerySchema = z.object({ "basis": z.enum(["adjusted","unadjusted"]).default("adjusted"), "end": z.string().date(), "known_as_of": z.union([InstantParamSchema, z.null()]).default(null), "period": FixedPeriodParamSchema, "start": z.string().date(), "ticker": TickerParamSchema }).strict();
export type SeriesQuery = z.infer<typeof SeriesQuerySchema>;

export const UniverseMemberSchema = z.object({ "security_id": z.string(), "ticker": z.string().describe("Ticker in use on the requested date.") }).strict();
export type UniverseMember = z.infer<typeof UniverseMemberSchema>;

export const UniverseSchema = z.object({ "date": z.string().date(), "securities": z.array(UniverseMemberSchema) }).strict().describe("Securities listed on a date. Delisted names drop out; their history stays.");
export type Universe = z.infer<typeof UniverseSchema>;

export const UniverseQuerySchema = z.object({ "date": z.string().date() }).strict();
export type UniverseQuery = z.infer<typeof UniverseQuerySchema>;
