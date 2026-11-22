# Circuit capacity implementation plan

> Execution: inline, using executing-plans and test-driven-development. User supplied the full specification and explicitly requested concrete changes and tests.

Goal: accept valid large QASM2 and restricted Python circuits, preserving existing API fields and RAG integrations.
Architecture: shared environment settings; schema distinguishes request shape from domain failures; AST interpreter (no execution of user Python); bounded subprocess per optimization; Qiskit transpilation and bounded equivalence; frontend consumes server limits and sends summaries to the LLM.
Tech stack: FastAPI/Pydantic, Qiskit 2.6.0, Next 16.4.6, Docker.
Spec: user request in this chat (sections 1–12).

## Evidence before changes
Live Docker: 47 operations/20 qubits -> 200; 501 operations -> 422 HTTP_ERROR inside worker; 2000 lines/1997 operations -> 422 INVALID_INPUT at body.qasm before handler. Schema and frontend cap 10000 characters, service caps 500 operations. Missing qasm and invalid syntax also produce 422, for separate reasons. Original historical payload is unavailable; the access-log line alone cannot identify which case occurred.

## Constraints and choices
Keep legacy qasm payload and response fields. Add code/format for Python or auto detection. Limits default to 2500 lines, 32 qubits, 10000 operations/depth, 524288 UTF-8 bytes, 60 seconds. Count directives in resource limits, expose quantum operation metrics separately. Restrict includes to qelib1.inc. AST only supports documented construction syntax; unsupported valid Python is a controlled error, never executed. Keep current single API worker. No full unitaries for large circuits.

## Review focus
- Nested/custom QASM operations, classical control and measurements must remain bounded and semantically preserved.
- AST calls, nested attributes, loops, numbers and containers must not escape or allocate without limits.
- Timeout/cancellation must reap workers and release slots; overload must reject, not queue without bounds.
- UTF-8 and JSON escaping must not cause contradictory limits or echo source in errors.
- Frontend must not send stale metrics or silently apply QASM as Python.

## Tasks
- [x] 1. Write endpoint capacity/validation/security tests; run red in Docker. Files: tests/test_circuit_capacity.py. Verify 50/500/2000 lines both formats, 20 qubits, 5000 operations, JSON/shape/syntax/limits, equivalence, timeout.
- [x] 2. Implement quantum/config.py and errors.py; safe python_parser.py and QASM loading. Preserve service functions. Verify actual metrics and security regression tests.
- [x] 3. Extend quantum/api.py, worker.py, main.py errors/body bound; expose /qiskit/limits. Test subprocess timeout/recovery/overload and structured error compatibility.
- [x] 4. Update frontend API/editor/results and context summaries. Consume server limits, retain format correctness. Run TypeScript/lint/build in Docker.
- [x] 5. Wire Docker env/resource limits, .env.example, docs and OpenAPI; run whole pytest suite and actual HTTP through Docker/Next proxy. Record any failures/limitations honestly.

## Progress
Initial repository clean. Work on fix/quantum-capacity in the user workspace, as requested. Docker engine accessible; host Python lacks dependencies, so use Docker test target. No LLM required for deterministic processing.

## Final verification — 2026-11-16

- Initial regression run: 25 failed / 1 passed before implementation.
- Final packaged test image (no host source mount): `docker run --rm --network none --memory 2g --cpus 2 --pids-limit 128 quantumone-capacity-test python -m pytest -q --tb=short -p no:cacheprovider` -> **157 passed, 1 skipped, 5 dependency deprecation warnings**, 154.77 s. Skipped test: `tests/test_live.py::test_fluxo_real`, opt-in live RAG test; no LLM calls or vector-database writes were performed for this task.
- Frontend: 4 Node tests passed; ESLint passed; Next 16.4.6 production build and TypeScript passed. A pre-existing SourceDrawer effect lint error was fixed by keyed component state reset.
- Compose validation and `git diff --check` passed. API runtime effective limits: 2 GiB, 2 CPUs, 128 processes. Both API and frontend rebuilt/recreated and healthy.
- Final live smoke: 14 cases against API plus 14 against Next proxy, all expected statuses. 2000-line/20-qubit inputs: API QASM 1.501 s / Python 1.594 s; proxy QASM 1.621 s / Python 1.499 s. 5000-operation inputs succeeded in both formats on both routes. These are generated-workload observations, not universal performance guarantees.
- Reports: `artifacts/quantum-capacity-api.json`, `artifacts/quantum-capacity-proxy.json`. Reproduce with `scripts/verificar_circuitos_http.py`.
- Independent review found two P2 issues: actual HTTP disconnect did not cancel the worker; leading-zero register literals could cause HTTP500. Both were reproduced with failing tests, fixed, and covered by the final green suite. First reviewer session was interrupted by an approval-service usage limit; a fresh focused review completed after resumption.

## Decisions and practical limits

- Ruling: interpret a documented Python construction subset instead of arbitrary Python execution — keeps security tractable; valid Python outside the subset returns UNSUPPORTED_PYTHON.
- Ruling: output QASM2 for both input formats and switch editor format when applied — preserves a single compatible output contract; does not reconstruct Python source structure.
- Ruling: materialize transpiler wire permutations and lower exporter extensions when needed — prevents semantic loss on export; final gate count can increase and is reported honestly.
- Ruling: matrix equivalence is bounded separately from accepted circuit size — avoids exponential allocations; large-circuit equivalence is explicitly unverified.
- No unresolved important review findings. No commits, merge or remote publication requested/performed; changes remain in the workspace branch for review.
