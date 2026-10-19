# Fetch and replay external evidence

## Available sources

`fetch tse` loads candidates for the 2024 municipal and 2026 general elections.
`fetch cnpj` captures and filters manually downloaded Receita CNPJ bulk files.
`fetch pdf --sample` captures invoice PDFs for sampled findings and extracts
their text. Run from `opsiu/` after `uv sync`. These commands apply pending schema
migrations before acquiring evidence.

Use the command help to inspect the available options without fetching data:

```sh
uv run python3 -m opsiu fetch tse --help
uv run python3 -m opsiu fetch cnpj --help
uv run python3 -m opsiu fetch pdf --help
```

## TSE inputs

```sh
uv run python3 -m opsiu fetch tse --ano 2024
uv run python3 -m opsiu fetch tse --ano 2026
```

The primary source catalogs are TSE's
[2024 candidates](https://dadosabertos.tse.jus.br/dataset/candidatos-2024) and
[2026 candidates](https://dadosabertos.tse.jus.br/dataset/candidatos-2026).
The required schema and ordinary first-round selection come from the official
candidate exports linked by these catalogs.

`--ano` selects 2024 or 2026. Downloads use `external.tse_2024_url` or
`external.tse_2026_url` from settings. `OPSIU_CONFIG` selects a TOML file, and
environment overrides such as `OPSIU_EXTERNAL__TSE_2024_URL` take precedence.
Use `OPSIU_PATHS__EXTERNAL_DIR`, `OPSIU_PATHS__DB_PATH`, and
`OPSIU_PATHS__RUNS_DIR` to isolate storage when testing. Relative paths are
resolved against the working directory.

## TSE raw snapshots and local replay

Each successful download creates an immutable indexed snapshot under
`paths.external_dir/tse_2024/<snapshot_id>/` or
`paths.external_dir/tse_2026/<snapshot_id>/`. It contains the raw `payload` and a
`manifest.json` with URL, safe response headers, SHA-256, byte count, fetch time,
and original run and step identifiers. Loaders consume the saved payload, so
parsing can be repeated without another HTTP request.

```sh
uv run python3 -m opsiu fetch tse --ano 2024 --replay
uv run python3 -m opsiu fetch tse --ano 2024 --snapshot-id SNAPSHOT_ID
```

`--replay` selects the latest indexed raw snapshot for that election year.
`--snapshot-id` selects one exact local snapshot belonging to that year's source.
The options are mutually exclusive and make no HTTP request. Replay validates
the local manifest and payload against the snapshot index before loading them.

The latest raw download and the latest successful candidate import are different
concepts. A successfully downloaded payload can fail parsing and remain available
as evidence. In that case, `--replay` selects that same latest raw payload again;
use an explicit known-good snapshot ID when deliberately replaying older input.

## TSE candidate loading and history

The loader reads semicolon-separated candidate CSV in Latin-1 or UTF-8 with a
BOM. ZIP payloads are read as streams without extracting members to disk. When
a national `BRASIL` candidate member exists, it is used instead of also loading
the state copies; otherwise the state members and any federal-scope `BR` member
are read. `BR` is not the national `BRASIL` aggregate.

Required columns are `SQ_CANDIDATO`, `ANO_ELEICAO`, `NR_CPF_CANDIDATO`,
`NM_CANDIDATO`, `SG_UF`, `DS_CARGO`, `DT_ELEICAO`, `NR_TURNO`, and
`CD_TIPO_ELEICAO`. Keep only the selected policy election date (`2024-10-06` or
`2026-10-04`), round `1`, and type `2` (`ELEIÇÃO ORDINÁRIA`). Other rounds and
supplementary elections are outside this import. Missing required columns and
malformed values fail rather than guessing dates or election types.

The `leiame.pdf` bundled with the official candidate archives defines `-4`
as a non-disclosable numeric value. CPF `-4` is stored as null, retaining the
candidate and original snapshot; R11 uses its existing name/UF fallback when
CPF is unavailable. Unknown malformed CPF values still fail publication.

Names are normalized consistently for candidate matching, while each stored
candidate retains its source member and record position. Repeated rows for the
same `SQ_CANDIDATO` are deduplicated when their candidate metadata agrees;
conflicting metadata for that identity fails publication. Migration `0005a_tse`
adds immutable candidate and import history with snapshot, run, and step origins.
`tse_imports_current` selects the latest successfully published import per year,
and `tse_candidatos_current` exposes only its candidates to the rule.

Import publication is atomic. Download or parse failures leave the previous
current import unchanged. A valid empty import makes that year's current
candidate set empty while retaining its previous history. An input whose SHA-256
matches the current import is skipped and retains the existing candidate origin.
A network invocation can still create a new raw snapshot before this skip is
determined.

## Receita inputs and manual download

Download the Receita CNPJ bulk files manually from the official
[CNPJ dataset catalog](https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj).
Use the published [Receita layout](https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf)
to identify the three required families: `Empresas`, `Socios`, and
`Estabelecimentos`. Keep the files from one release together in a local
directory. `fetch cnpj` does not download the national bulk dataset over HTTP.

The files are headerless Latin-1 CSV with semicolon delimiters and respectively
7, 11, and 30 fields; a UTF-8 BOM also selects the supported UTF-8 encoding.
Companies and partners are associated with the eight-digit
CNPJ root; establishments supply the order and check digits needed for a full
14-digit CNPJ and its principal CNAE. Using establishments prevents assigning
one branch's principal activity to another branch of the same company.

Ingest the reimbursement years first, then supply the local bulk directory:

```sh
uv run python3 -m opsiu ingest
uv run python3 -m opsiu fetch cnpj --from /path/to/receita-release
```

All three families are required for this import. Keep source files and their
names intact; do not add a header or combine columns into a custom format.
Retain all downloaded partitions for the selected release. A locally supplied
subset cannot establish that the complete national release was searched.

The directory is scanned at its top level. Accepted inputs are family-prefixed
ZIP or CSV names, such as `Empresas0.zip`, `Socios0.zip`, and
`Estabelecimentos0.zip`, or extracted files with the official `.EMPRECSV`,
`.SOCIOCSV`, and `.ESTABELE` suffixes. ZIP members are streamed without extracting
them to disk. Supply either the archive or its extracted equivalent, avoiding
duplicate copies of the same records. Unsafe archive paths, duplicate member
names, and members from a different family fail loading.

## Receita filtering, replay, and history

Capture creates `paths.external_dir/cnpj/<snapshot_id>/manifest.json` and raw
copies under its `files/` directory. The manifest preserves the original input
directory, capture time, run and step, and each filename, family, original path,
SHA-256, and byte count. Its aggregate hash includes the sorted file inventory
and each file's content identity. No existing snapshot is overwritten.

The loader scans the bulk rows as streams and stores only records whose numeric
CNPJ roots occur in `despesas_current`. This limits retained data to suppliers
in the current reimbursement snapshots. It emits progress events every one
million scanned records. The selected root set belongs to the normalized import
and is part of the decision to reuse or reload its contents.
This version selects the numeric CNPJs represented in CEAP; it does not strip
letters from other roots to manufacture numeric matches.

```sh
uv run python3 -m opsiu fetch cnpj --replay
uv run python3 -m opsiu fetch cnpj --snapshot-id SNAPSHOT_ID
```

Exactly one of `--from`, `--replay`, and `--snapshot-id` is required. Replay uses
the saved raw files without another download or dependence on the original
input directory. `--replay` selects the latest local raw snapshot; the explicit
ID selects an exact snapshot. Source hashes are validated before reuse.

Migration `0005b_cnpj` preserves local snapshot metadata, import history, root
coverage, companies, partners, and establishments. Publication is atomic:
failed capture or normalization preserves the previous successful import.
Current views select successfully published normalized data, while earlier
snapshots and imported rows remain available as evidence.

The import records every requested root, including roots with no matching rows
in the supplied files. A covered root without company or partner data is a valid
no-match result. Coverage identifies the searched root set and supplied input,
not independent proof that every national partition was present. Conflicting
company or establishment metadata for the same identity fails publication;
identical duplicates retain one deterministic source row. Partner rows preserve
their source positions.

When both the raw content hash and selected roots match the current import,
normalization is skipped and its original provenance is retained. New current
expense roots require filtering again, even when the bulk files are unchanged.
That reload can use the original raw snapshot ID, keeping the external evidence
identity stable for suppliers already present. The snapshot is not rewritten to
add filtered database rows.

A raw capture can succeed before normalization fails. The latest raw snapshot
can therefore differ from the one driving current views; use an explicit
known-good ID when replaying an older input. A valid import with no retained rows
still publishes its requested-root coverage and replaces the current filtered
view while retaining earlier history.

The imported bulk data describes the supplied release. It does not reconstruct
the company's legal name, establishment activity, or ownership on each expense
date. R12 and R13 therefore produce review indicators with that limitation.

## PDF selection and bounded sampling

Run a publishing [check](check.md), then fetch the invoice sample:

```sh
uv run python3 -m opsiu check
uv run python3 -m opsiu fetch pdf --sample
```

`--sample` is required. The reusable sampler selects the latest terminal `check`
run with status `completed` or `completed_with_errors`, excluding dry runs.
Only rules with a `rule.checked` event from a completed step in that run are
eligible. Failed or skipped rule steps contribute no sample. If that latest
eligible check has no successful rules, the sample is empty; it does not fall
back to an older check with findings.
If no eligible terminal check exists, the sampling step fails with its reason
recorded. Omitting `--sample` is a configuration error.

For each eligible rule and version, select active findings by amount descending,
then document ID and finding key ascending. The limit is `sampling.per_rule`,
default `20`. For example:

```sh
OPSIU_SAMPLING__PER_RULE=5 uv run python3 -m opsiu fetch pdf --sample
```

Unchanged findings retain the run in which they were originally created.
Sampling therefore uses the latest check's successful rule IDs and versions,
not a requirement that each finding's stored `run_id` equal that check's ID.
The source expense and `urlDocumento` come from the finding's original
provenance, preserving the evidence even after a forced expense reload.

Findings referring to the same document ID and original URL share one PDF
operation within the run. A missing URL is recorded as skipped. Each document
has an independent step, so a download or extraction failure does not prevent
the remaining sample from being processed. The limit bounds selected findings;
deduplication and missing URLs can reduce the number of downloads.

## PDF snapshots, cache, and refresh

Each download uses the HTTP snapshot store and saves raw response bytes under
`paths.external_dir/pdf/<snapshot_id>/payload`, with its `manifest.json`.
The raw snapshot retains content encoding when supplied by the server. A
separate decoded PDF is cached for extraction.

The first cache path is `paths.external_dir/pdf/<id_documento>.pdf`, created
exclusively. Later URLs or explicit refreshes use
`paths.external_dir/pdf/versions/<id_documento>/<snapshot_id>.pdf`. Existing
files and extracted text are never overwritten.
Published cache paths remain reserved even if their files are deleted; later
downloads use a new version path instead of reusing a historical location.

Ordinary reruns validate the raw snapshot and the decoded PDF's recorded
SHA-256 and size before reusing the cache. A corrupted cache fails its document
step; it does not silently replace the file or download another copy. An
explicit refresh obtains new evidence while preserving the old artifact:

```sh
uv run python3 -m opsiu fetch pdf --sample --refresh
```

Refresh remains subject to the same sampling limit. It always captures a new
raw snapshot and a new decoded PDF version, even if the server returns identical
bytes. It does not expand the sample to every finding or change finding approval.

## PDF extraction and provenance

`pypdf` extracts text from the decoded PDF. Migration `0006_nf_texto` stores
append-only extractions with the document ID, original URL, raw snapshot
identity/hash, decoded file path/hash/size, extractor name/version, text/hash,
page count, `needs_ocr`, and extraction run/step. `nf_texto_findings` links an
extraction to every sampled finding that used it, retaining the finding's
original expense provenance.

An unchanged extractor can reuse its existing extraction after its text hash
and metadata are validated. When the extractor
version changes, verified cached PDF bytes are extracted again without HTTP,
and a new extraction row is appended. Earlier text, metadata, and finding links
remain available.

Empty extracted text sets `needs_ocr = true`. This is a flag for later handling;
no OCR is performed by this command. Nonempty text does not guarantee accurate
or complete extraction, and a malformed PDF is a recorded failure rather than
an empty-text success. PDF fetching and extraction do not decide whether a rule
was broken, annotate findings with an agent, or approve a final report.

## Runs and failure diagnosis

Each command execution prints its run ID first and its `report.md` path last.
Finalization automatically creates `paths.runs_dir/<run_id>/report.md`, including
the command, arguments, duration, step outcomes, counters, failure messages,
and registered artifacts. Failed and aborted executions also receive reports
when finalization can write them. The report links to `manifest.json`,
`events.jsonl`, and `log.jsonl` for the selected year, snapshot identities,
import outcome, and further diagnosis.

```sh
uv run python3 -m opsiu runs show RUN_ID --events
```

`runs show` prints the report path. See [run history and reports](runs.md) for
the full inspection workflow, parser-level failures, and report-write failures.

Unsupported years or conflicting replay options are configuration errors.
Unavailable HTTP sources, corrupt snapshots, malformed source files, or
database failures produce a recorded failed step. Exit code `1` denotes an
operational failure; configuration errors exit `2`; a completed run exits `0`.

After correcting an operational issue, rerun the command or replay the intended
snapshot explicitly. Do not alter raw snapshot files to repair them: replay
checks their hashes, and earlier snapshots and imports are retained as evidence.

## Using the imported evidence

Load the corresponding reimbursement years with [ingest](ingest.md), then run:

```sh
uv run python3 -m opsiu check --regras R11
uv run python3 -m opsiu report findings
```

R11 evaluates the pre-election publicity window using the latest successful
candidate import for each election year. Candidate matching and its limitations
are documented in [R11](../../.llms/rules/R11-divulgacao-eleitoral.md).
A relevant in-window expense with no successful import for its election year
fails the rule; a successfully imported empty candidate set is a valid no-match
result. These outcomes preserve the distinction between unavailable evidence
and an imported dataset with no matching candidate.

For Receita evidence, run:

```sh
uv run python3 -m opsiu check --regras R12,R13
uv run python3 -m opsiu report findings
```

[R12](../../.llms/rules/R12-sobrenome-socio.md) compares eligible surname tokens
with individual partners. A shared token does not establish kinship or ownership
at the expense date. [R13](../../.llms/rules/R13-fornecedor-incompativel.md) checks
explicit grocery and driver-service indicators in the Receita legal name or
the exact establishment's principal CNAE. It does not establish the goods or
services actually included in an invoice. Both use severity `revisar`.

Both rules fail when a relevant root lacks coverage in a successful current
import. R12 checks numeric-CNPJ expenses across all categories; R13 checks only
categories `13` and `120`. After ingesting expenses with new supplier roots,
replay the bulk snapshot to extend coverage before checking. A covered root
with no matching data differs from a root that was never included in the import.

Importing a different snapshot can change a finding's external evidence without
changing `(rule_id, rule_version, id_documento)`. A subsequent check can therefore
fail with the immutable-conflict behavior in
[ADR 0009](../adr/0009-immutable-finding-conflicts.md). Preserve the earlier finding
and review history rather than overwriting them. [`report findings`](report.md)
exports all active findings regardless of their human decision; it does not
grant approval. Use [review](review.md) to record decisions and `report final`
to export active findings whose latest decision is `aprovado`.

## Current Receita download location

The former `/dados/cnpj/dados_abertos_cnpj/` URL returns 404. The official
[CNPJ share](https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9)
is the current catalog. Select one monthly release and download every Empresas,
Socios, and Estabelecimentos partition into one directory, then use
`fetch cnpj --from` as above. The catalog URL is not a ZIP file; the CLI still
imports a supplied local release and does not automatically download the catalog.
