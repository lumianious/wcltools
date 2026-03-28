---
phase: 9
slug: m-benchmark-aggregation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-28
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | pyproject.toml or pytest.ini (if exists) |
| **Quick run command** | `python -m pytest tests/ -x -q` |
| **Full suite command** | `python -m pytest tests/ -v` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 09-01-01 | 01 | 1 | BENCH-02, DMG-01 | unit | `python -m pytest tests/test_mplus_benchmarks.py -x -q` | ❌ W0 | ⬜ pending |
| 09-01-02 | 01 | 1 | CD-01, CD-02 | unit | `python -m pytest tests/test_mplus_benchmarks.py -x -q` | ❌ W0 | ⬜ pending |
| 09-01-03 | 01 | 1 | SURV-01, INT-01 | unit | `python -m pytest tests/test_mplus_benchmarks.py -x -q` | ❌ W0 | ⬜ pending |
| 09-02-01 | 02 | 1 | BENCH-03 | unit | `python -m pytest tests/test_mplus_benchmarks.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_mplus_benchmarks.py` — stubs for BENCH-02, BENCH-03, CD-01, CD-02, DMG-01, SURV-01, INT-01
- [ ] `tests/conftest.py` — shared fixtures (WCL client mock, sample report data)
