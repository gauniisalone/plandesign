// Génère src/api/types.gen.ts depuis les JSON Schema exportés par `openplan schemas`.
// Usage : node scripts/gen-types.mjs <dossier_schemas>
//
// Chaque schéma embarque ses propres $defs (Point2, Wall, ...). On compile chaque fichier
// entièrement puis on ne conserve qu'une déclaration par nom : les définitions sont
// structurellement identiques d'un schéma à l'autre puisqu'elles viennent des mêmes
// modèles Pydantic.
import { compile } from "json-schema-to-typescript";
import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const inDir = process.argv[2] ?? "schemas";
const outFile = "src/api/types.gen.ts";

const files = (await readdir(inDir)).filter((f) => f.endsWith(".schema.json")).sort();
if (files.length === 0) {
  console.error(`aucun *.schema.json dans ${inDir} : lancer d'abord \`make web-types\``);
  process.exit(1);
}

const declared = new Set();
let out =
  "/* Généré par scripts/gen-types.mjs depuis les JSON Schema Pydantic. Ne pas éditer. */\n" +
  "/* eslint-disable */\n\n";

for (const file of files) {
  const schema = JSON.parse(await readFile(path.join(inDir, file), "utf8"));
  const ts = await compile(schema, schema.title ?? file, {
    bannerComment: "",
    additionalProperties: false,
    declareExternallyReferenced: true,
  });
  for (const block of ts.split(/\n(?=export (?:interface|type) )/)) {
    const match = block.match(/^export (?:interface|type) (\w+)/);
    const name = match?.[1];
    if (name && declared.has(name)) continue;
    if (name) declared.add(name);
    if (block.trim()) out += block.trim() + "\n\n";
  }
}

await mkdir(path.dirname(outFile), { recursive: true });
await writeFile(outFile, out, "utf8");
console.log(`${outFile} : ${declared.size} déclarations`);
