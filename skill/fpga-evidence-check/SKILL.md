---
name: fpga-evidence-check
description: Seal selected FPGA or MCU release artifacts and compare numeric golden-reference CSV results with a standalone offline checker. Use for reproducible handoff, changed-input detection and regression comparison, not hardware programming or certifying design correctness from hashes.
---

# FPGA evidence and handoff checks

Use the bundled Python 3.10+ standard-library checker. No board, Vivado, serial port, original project context, or assistant installation is required.

## Select the evidence scope

- Explicitly select release input files, run `scripts/evidence_check.py seal`, then `check` on the received directory. Matching selected bytes do not prove source completeness, rebuild success, or FPGA programming. Unlisted files are outside the check.
- Normalize reference and actual results to `id,value` CSV, then run `compare`. Choose absolute tolerance from the specification before seeing the result, not to make failures pass. Missing/duplicate/non-finite values are not zeros.
- For board/transport trouble read [references/triage.md](references/triage.md). It requires device-specific state/save adapters; this checker never accesses hardware.

From this skill directory, run:

```text
python -B scripts/evidence_check.py compare --expected examples/counter_expected.csv --actual examples/counter_actual.csv
python -B scripts/evidence_check.py seal --root examples --file counter_expected.csv --out baseline.json
python -B scripts/evidence_check.py check --root examples --manifest baseline.json
python -B scripts/test_evidence_check.py
```

CSV requires UTF-8, exactly `id,value`, unique nonempty unpadded IDs, and finite decimal values. Default absolute tolerance is zero; `--atol` accepts a nonnegative decimal. Manifest outputs must not exist. Exit 0 means agreement, 1 valid-input mismatch, 2 invalid/unavailable input. Selected paths must be relative, without parent traversal, links or reparse points. Decimal digits and absolute exponent are each limited to 4096; large CSV files are loaded into memory.

Read [references/validation.md](references/validation.md) for tested behavior and [references/origins.md](references/origins.md) for actual observations. Example data are synthetic offline fixtures, not new RTL simulations or physical measurements.

## Invariants

Keep source, golden reference, actual result, configuration and firmware identity distinct. Report integrity, numeric agreement, build and board qualification separately. An old report does not qualify a new image merely because names match.

Preserve old manifests and failed reports. Use a new explicit output filename; never reseal changed files and call them the original baseline. The checker refuses overwrite, unsafe relative paths and links. Avoid publishing private paths or unredacted conversations.

## Reuse beyond the original topic

No Timing Sentinel protocol, board or pin dependencies exist in the checker. It supports counter, FIR, interface-bridge regression exports and MCU/PC/FPGA handoffs. Other formats need a separately verified converter; hashes cannot validate its algorithm. Two unrelated synthetic datasets and a relocated minimal deployment demonstrate offline reuse, not unperformed cross-board tests.
