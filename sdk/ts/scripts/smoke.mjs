// Smoke test against a running server: `uv run demo serve` then `npm run build && node scripts/smoke.mjs`.
import { PitApiError, PitClient } from "../dist/index.js";

const client = new PitClient(process.env.PITDEMO_URL ?? "http://127.0.0.1:8000");
const r = await client.consensus("NVDA", "FY2025", "2024-05-31");
console.log(`NVDA FY2025 as of 2024-05-31 (${r.basis}): mean ${r.mean?.toFixed(3)} from ${r.count} estimates`);
console.log(`  basis: ${r.target_basis}`);
const s = await client.consensusSeries("NVDA", "FY2025", "2024-06-03", "2024-06-14");
console.log(`  series points: ${s.points.length}`);
try {
  await client.consensus("SIVB", "FY2023", "2023-06-01");
} catch (e) {
  if (e instanceof PitApiError) console.log(`  typed error: ${e.error.error}`);
  else throw e;
}
