import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import process from "node:process";
import openapiTS, { astToString, COMMENT_HEADER } from "openapi-typescript";

const root = resolve(import.meta.dirname, "..");
const schemaPath = resolve(root, "openapi.json");
const outputPath = resolve(root, "src/generated/openapi.ts");
const schema = JSON.parse(await readFile(schemaPath, "utf8"));
const generated = `${COMMENT_HEADER}${astToString(await openapiTS(schema))}`;

if (process.argv.includes("--update")) {
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, generated, "utf8");
  console.log("Updated src/generated/openapi.ts");
} else {
  let current = "";
  try { current = await readFile(outputPath, "utf8"); } catch { /* reported below */ }
  if (current !== generated) {
    console.error("Generated API types are stale. Run `npm run api:generate` and commit the result.");
    process.exitCode = 1;
  } else {
    console.log("API-003 generated frontend types are current.");
  }
}
