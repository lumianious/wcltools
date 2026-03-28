---
phase: 10
slug: m-comparison-engine
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-29
---

# Phase 10 — Validation Strategy

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
| 10-01-01 | 01 | 1 | DMG-02 | unit | `python -m pytest tests/test_mplus_comparison.py -x -q` | ❌ W0 | ⬜ pending |
| 10-01-02 | 01 | 1 | SURV-02, INT-02 | unit | `python -m pytest tests/test_mplus_comparison.py -x -q` | ❌ W0 | ⬜ pending |
| 10-02-01 | 02 | 2 | BOSS-01, BOSS-02 | unit | `python -m pytest tests/test_mplus_comparison.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_mplus_comparison.py` — stubs for DMG-02, BOSS-01, BOSS-02, SURV-02, INT-02
- [ ] `tests/conftest.py` — shared fixtures (extend existing with comparison mock data)
