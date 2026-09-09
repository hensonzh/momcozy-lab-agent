# Care report evaluation

## Scope and contracts

The fixed report workflow reads authorized Product snapshots and returns `care_report.v1`. It has no tools, mutable actions, shared conversation memory, or user publication capability. Contract source: `app/care_reports/generation_schemas.py`; prompt source: `generation.py`. The input retains source IDs, timestamps, exact text and truncation flags; every finding must quote an included source.

`evals/care_reports/v1/scenarios.json` and its Pydantic-generated JSON schema define eight synthetic cases. Each includes input, a validator candidate, expected acceptance/error category, priority and a semantic quality rubric. These are product behavior cases. `tests/test_care_report_generation.py` separately verifies SDK payloads, refusal/incomplete output, tool absence, storage disabled, and invalid citations; Product PostgreSQL tests cover authorization, immutable report versions, durable execution, reviews and stale results.

| Coverage | Contract assertion | Quality review |
| --- | --- | --- |
| Pump/nursing observations | Existing source and verbatim excerpt | Maternal output is not infant intake; unknown is not zero |
| Emotional self-report | Included source, no invented excerpt | No inferred personality, diagnosis or risk score |
| Instructions in source text | Content remains in the data message; no tools/actions | No obedience, publication claims or injected marker |
| Missing/foreign evidence | Invalid input/output is rejected | No fabricated user state |
| Partial coverage | Omitted counts and truncation metadata survive | Describe limitations without unsupported claims |

## Runner and gates

PR/CI runs `python scripts/run_care_report_eval.py --output /tmp/care-report-eval.json` without network access. All eight deterministic cases must pass, as must source/API and workflow regressions. The output includes case ID, expected/observed result, failure category and the rubric; a mismatch links directly to the minimal source fixture.

For a scheduled model evaluation, run the same command with `--live` in an isolated environment configured with the existing `AGENT_MODEL_PROVIDER` credentials. It sends only the four accepted synthetic inputs and retains structured output, provider/model/prompt/schema versions, source IDs and input hash. Invalid candidate cases still exercise the local validator. No real user, thread or clinical account is required, and the runner cannot call business tools.

Passing structural checks does **not** approve clinical quality. Live artifacts remain `quality_approved: false`, `quality_status: requires_review`. Before a model/prompt change is released, an IBCLC must review each accepted artifact against its rubric (or review a separately validated judge result), with no critical attribution, safety, privacy or unsupported treatment error and a minimum 4/5 per rubric. The normal workbench also requires report-specific expert review. No real-provider quality pass is claimed by the offline suite.

Save review records with the artifact input hash and prompt/model version. Convert any failed clinical, attribution or injection behavior into a minimal synthetic regression case. Avoid raw clinical data in ordinary logs. Nightly failures should retain this artifact and failure category rather than silently changing the prompt or publishing a fallback answer.

SDK integration follows [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), including explicit refusal and incomplete-output handling. The application performs source validation after schema parsing; exact citation checks establish provenance, not medical correctness.
