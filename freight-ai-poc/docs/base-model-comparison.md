# Base-model comparison

Historical 18-case intent regression. No fine-tuning. CPU; greedy decoding; shared prompts and cases.

| Model | Strategy | Schema valid | Exact intent | Mean generation seconds |
|---|---|---:|---:|---:|
| 0.5b | base_zero | 5.6% | 0.0% | 3.82 |
| 0.5b | base_one | 50.0% | 0.0% | 3.05 |
| 0.5b | base_few | 22.2% | 0.0% | 3.03 |
| 1.5b | base_zero | 44.4% | 0.0% | 5.60 |
| 1.5b | base_one | 66.7% | 0.0% | 7.89 |
| 1.5b | base_few | 66.7% | 44.4% | 8.05 |
| 3b | base_zero | 44.4% | 11.1% | 9.12 |
| 3b | base_one | 66.7% | 22.2% | 14.32 |
| 3b | base_few | 88.9% | 44.4% | 14.37 |

Conversation outputs are separate development assessments; scripted query success must not be attributed entirely to model capability. No expert-reviewed or blind quality score is claimed.

Recommendation: 1.5B few-shot is the next experimental intent baseline. Both 1.5B and 3B achieve 8/18 exact matches; 3B offers higher schema validity at greater observed CPU cost. Neither is production-ready. See conversation-review.md for factual errors and review limitations.

## Resource measurements

| Model | Peak process RSS (GiB) | Total run seconds |
|---|---:|---:|
| 0.5b | 3.14 | 181.3 |
| 1.5b | 9.01 | 391.0 |
| 3b | 12.87 | 685.4 |

Single runs on the local CPU, not a throughput benchmark. RSS includes loading and verification, and does not measure minimum required memory. Verification was changed from whole-file reads to streaming between early and later runs; memory figures are observed process peaks, not strictly isolated inference memory. Timing excludes file verification but includes model loading. No claim about GPU performance or hosted-model cost.
