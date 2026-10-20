import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const directory = dirname(fileURLToPath(import.meta.url));
const outputDirectory = join(
  directory,
  "../../src/opsiu/interfaces/web/static",
);

execFileSync(
  process.execPath,
  [
    join(directory, "node_modules/esbuild/bin/esbuild"),
    "entry.js",
    "--bundle",
    "--format=iife",
    "--global-name=PIXI",
    "--minify",
    "--target=es2020",
    "--platform=browser",
    "--legal-comments=inline",
    "--metafile=metafile.json",
    "--banner:js=/*! OPSIU PixiJS 8.22.0 browser bundle. Licenses: pixi.LICENSE.txt */",
    `--outfile=${join(outputDirectory, "pixi.min.js")}`,
  ],
  { cwd: directory, stdio: "inherit" },
);

const metadata = JSON.parse(
  readFileSync(join(directory, "metafile.json"), "utf8"),
);
const packages = new Set(
  Object.values(metadata.outputs)
    .flatMap((output) =>
      Object.entries(output.inputs)
        .filter(([, input]) => input.bytesInOutput > 0)
        .map(([path]) => path.match(/^node_modules\/((?:@[^/]+\/)?[^/]+)/)?.[1]),
    )
    .filter(Boolean),
);
const bundledLicenses = {
  "pixi.js": "node_modules/pixi.js/LICENSE",
  "eventemitter3": "node_modules/eventemitter3/LICENSE",
  "@pixi/colord": "licenses/colord.LICENSE",
  "earcut": "node_modules/earcut/LICENSE",
  "ismobilejs": "node_modules/ismobilejs/LICENSE",
  "parse-svg-path": "node_modules/parse-svg-path/LICENSE",
  "tiny-lru": "node_modules/tiny-lru/LICENSE",
};
if (
  packages.size !== Object.keys(bundledLicenses).length ||
  [...packages].some((name) => !(name in bundledLicenses))
) {
  throw new Error("Bundled dependency licenses changed; review build.mjs.");
}

const notices = Object.entries(bundledLicenses).map(([name, path]) => {
  const { version } = JSON.parse(
    readFileSync(join(directory, "node_modules", name, "package.json"), "utf8"),
  );
  return [
    `${name} ${version}`,
    "=".repeat(72),
    readFileSync(join(directory, path), "utf8").trimEnd(),
  ].join("\n");
});
writeFileSync(
  join(outputDirectory, "pixi.LICENSE.txt"),
  `${notices.join("\n\n")}\n`,
);

for (const filename of ["pixi.min.js", "pixi.LICENSE.txt"]) {
  const bytes = readFileSync(join(outputDirectory, filename));
  console.log(
    `${filename}: ${bytes.length} bytes, SHA-256 ${createHash("sha256").update(bytes).digest("hex")}`,
  );
}
