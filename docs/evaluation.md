# Evaluation

## Dataset And Method

`evals/questions.json` contains 20 public technical questions about current APIs, orchestration, security, and evaluation. Several questions intentionally require date/version checks. They contain review prompts rather than prefilled “ground truth” for changing facts. Review each answer against dated sources before scoring it.

The opt-in command runs each selected question through the four-role graph and a simple single-agent tool-calling baseline. Both use the configured OpenAI model, Tavily Search, guarded fetcher, and the same Quick/Standard per-run ceilings. Each method gets its own per-run budget. No paid calls happen unless `--run-live` is present:

```powershell
cd backend
python -m app.evaluation --run-live --question-limit 1 --depth quick
```

Increase `--question-limit` deliberately, up to 20. Measurements are written to ignored `backend/evaluation-results/measurements.json` and `measurements.csv`. The graph records structural reference rate, citation coverage, passage validity, fetch success, request counts, and latency. The baseline records provider usage metadata when available, request/search/fetch counts, answer length, and latency. It is not a fifth app role.

Structural measures are not semantic quality. A valid citation can fail to support a sentence; a source can be current but irrelevant; a useful answer can be incomplete. Compare results only after manual review.

## Human Review Template

Copy this template into a separate review record and identify the reviewer/date:

```text
Question ID:
Run ID and method:
Reviewer and review date:
Question interpretation / required scope:
Are cited sources accessible and dated/versioned where needed? (yes/no/unknown)
Does each cited excerpt directly support the associated claim? (yes/no/partial)
Any material counterevidence omitted?
Any source independence/syndication concern?
Completeness for the question (1-5) and reason:
Scope/version/date accuracy (1-5) and reason:
Unsupported or overstated claims (list):
Important missing evidence (list):
Would you rely on this report for this use? Why/why not:
```

## Report And Limitations

Compare paired questions, actual latency and provider token counts when available, budget exhaustion, fetch success, structural references, and the human review forms. Do not add an LLM judge as unquestionable ground truth. Keep prompt/model versions, dates, and tool settings with any manually reviewed results. Do not publish results from a different model or budget as if they were comparable. No evaluation results or accuracy claims are prefilled here.
