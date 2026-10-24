// Generate src/schemas.ts (Zod) from ../schema.json (exported from the Python models).
// Each $def becomes an exported `<Name>Schema`; $refs point at those constants, and the
// definitions are emitted in dependency order.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { jsonSchemaToZod } from "json-schema-to-zod";

const here = dirname(fileURLToPath(import.meta.url));
const schema = JSON.parse(readFileSync(join(here, "..", "..", "schema.json"), "utf8"));
const defs = schema.$defs;
const refName = (ref) => ref.replace("#/$defs/", "");

function deps(node, acc = new Set()) {
  if (Array.isArray(node)) node.forEach((n) => deps(n, acc));
  else if (node && typeof node === "object") {
    if (typeof node.$ref === "string") acc.add(refName(node.$ref));
    Object.values(node).forEach((v) => deps(v, acc));
  }
  return acc;
}

const order = [];
const seen = new Set();
function visit(name) {
  if (seen.has(name)) return;
  seen.add(name);
  [...deps(defs[name])].sort().forEach(visit);
  order.push(name);
}
Object.keys(defs).sort().forEach(visit);

const parserOverride = (node) =>
  typeof node.$ref === "string" ? `${refName(node.$ref)}Schema` : undefined;

let out =
  "// Generated from sdk/schema.json by `npm run gen`. Do not edit by hand.\n" +
  'import { z } from "zod";\n';
for (const name of order) {
  const body = jsonSchemaToZod(defs[name], { module: "none", parserOverride }).trim();
  out += `\nexport const ${name}Schema = ${body.replace(/;$/, "")};\n`;
  out += `export type ${name} = z.infer<typeof ${name}Schema>;\n`;
}
const target = join(here, "..", "src", "schemas.ts");
if (process.argv.includes("--check")) {
  let current = "";
  try {
    current = readFileSync(target, "utf8");
  } catch {}
  if (current !== out) {
    console.error("src/schemas.ts is out of date with sdk/schema.json: run `npm run gen`");
    process.exit(1);
  }
  console.log("src/schemas.ts is up to date");
} else {
  writeFileSync(target, out);
  console.log(`wrote src/schemas.ts (${order.length} schemas)`);
}
