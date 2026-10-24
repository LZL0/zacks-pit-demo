// End-to-end check of the built TS client against a running server.
// `uv run demo serve` (or CI's scripts/ci/smoke.py), then:
//   npm run build && PITDEMO_URL=http://127.0.0.1:8000 node scripts/smoke.mjs
// Every response is parsed by the generated Zod schemas inside the client, so a
// schema mismatch fails here too. Exits non-zero on any failed check.
import { PitApiError, PitClient } from "../dist/index.js";

const client = new PitClient(process.env.PITDEMO_URL ?? "http://127.0.0.1:8000");
let failures = 0;
function check(ok, message) {
  console.log(`${ok ? "ok  " : "FAIL"} ${message}`);
  if (!ok) failures += 1;
}

const r = await client.consensus("NVDA", "FY2025", "2024-05-31");
check(r.basis === "adjusted" && r.security.security_id === "SEC-0001", "consensus: typed result");
check(r.mean !== null && r.mean > 1 && r.mean < 5, `consensus: post-split mean ${r.mean}`);
check(r.count >= 3 && !r.insufficient, `consensus: ${r.count} estimates`);
check(r.as_of.endsWith("Z"), `consensus: explicit UTC as_of ${r.as_of}`);

const u = await client.consensus("NVDA", "FY2025", "2024-05-31", "unadjusted");
check(u.mean !== null && r.mean !== null && Math.abs(u.mean / r.mean - 10) < 1e-6,
  "unadjusted is 10x adjusted before the 10-for-1 split");

const s = await client.consensusSeries("NVDA", "FY2025", "2024-06-03", "2024-06-14");
check(s.points.length === 10, `series: ${s.points.length} trading days`);

const a = await client.corporateActions("GE");
check(a.actions.some((x) => x.action_type === "SPINOFF" && x.ratio === 1), "corporate actions: GE spin-off, no share factor");

const e = await client.explain("NVDA", "2024-06-14", "FY2025");
check(e.narrative.some((line) => line.includes("SPLIT ratio 10")), "explain: split narrative");

for (const [args, kind, status] of [
  [["SIVB", "FY2023", "2023-06-01"], "NotFound", 404],
  [["NVDA", "next", "2024-05-31"], "InvalidRequest", 400],
]) {
  try {
    await client.consensus(...args);
    check(false, `${kind}: expected an error`);
  } catch (err) {
    check(err instanceof PitApiError && err.status === status && err.error?.error === kind, `typed error ${kind}`);
  }
}

if (failures > 0) {
  console.error(`${failures} check(s) failed`);
  process.exit(1);
}
console.log("TS SDK smoke test passed");
