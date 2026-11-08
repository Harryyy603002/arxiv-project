# Testing and performance

## Verified test command

```text
pytest src/tests
14 passed
```

The verified run used Python 3.14.7. No coverage percentage is configured.

## Test inventory

- `test_m1_metadata.py`: ID parsing, version manifests, metadata persistence, papers CSV.
- `test_m1_references.py`: batch payloads, cache behavior, 413 splitting, integrated references.
- `test_m2_parser.py`: BibTeX parsing, deduplication, citation rewriting, candidates.
- `test_run_m1_downloader.py`: source checkpoints, archive extraction/byte accounting, overlap.

## Benchmark instrumentation

`utils.benchmark` contains `ResourceMonitor` and `PerfRecorder`. It can measure stage/paper time, process-tree RSS, watched-directory size, source archive bytes, reference counts/success, and deduplication counts. `before_bytes` means all uncompressed archive members; `after_bytes` means retained `.tex`/`.bib` bytes.

No benchmark output is committed. Runtime, memory, disk, throughput, assigned-ID coverage, and 20-hour feasibility remain `[TO BE VERIFIED]` by a controlled live run.

The team plan schedules performance measurement in parallel with M1/M2 work:
P4 owns `psutil`/time instrumentation, M1 crawl statistics, M2 before/after
deduplication statistics, and report integration. This documents ownership and
planned timing only; it does not supply missing measurements.

## Known issues and limitations

- M2/M3 entry points are not runnable.
- `utils.schemas` is a stub.
- M1 integrated reference-table export is a no-op.
- M1 path conventions differ.
- Candidate empty-string similarity can be misleading.
- Exact-key BibTeX merge does not produce replacements.
- BibTeX parsing is lightweight, not a complete grammar.
- No live data or final dataset is present.

## Reproducibility

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r src\requirements.txt
pytest src\tests
```

For M1, set `ARXIV_DATA_DIR` to an ignored writable directory, optionally set `S2_API_KEY` and `CONTACT_EMAIL`, and provide an ID file. Do not commit keys or data. M2/M3 cannot currently be reproduced end-to-end.

## Team review

- Validation owner: `[TEAM INPUT REQUIRED]`
- Reviewer: `[TEAM INPUT REQUIRED]`
- Benchmark dataset and hardware: `[TEAM INPUT REQUIRED]`
- Additional notes: `[TEAM INPUT REQUIRED]`

## Planned validation milestones

- M1 target completion: 24/10/2026.
- M2 target completion: 30/10/2026.
- M3 and poster target completion: 05/11/2026.
- Formal submission target: 06/11/2026.

These dates come from the team plan and should not be confused with verified
execution timestamps.


---
