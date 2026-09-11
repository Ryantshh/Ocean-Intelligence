# Conversation development assessment

These are 10 turns per model across eight conversations, authored during development.
They are not a blind test or a domain-expert benchmark. Review below is an engineering
inspection against the project's reference definitions and computed tool outputs.
Raw transcripts are in artifacts/evaluation/conversation-*.json.

## Completed 0.5B and 1.5B observations

Both produced the same six deterministic data responses: Singapore lookup (one
vessel), Rotterdam lookup (one order), 175,000 DWT lookup (one vessel), Tubarao
screen (one candidate requiring review), 90% capacity follow-up (zero candidates),
and changing the order to Santos (two candidates under default settings).
These successes come from application parsing and the deterministic engine, so
must not be counted as independent evidence of model reasoning.

An assumption applied in one turn is not persisted in structured intent: switching
to Santos restores the configured capacity assumption. Persistent what-if scenarios
remain a conversation-state limitation, even though the order reference changes.

| Question | 0.5B | 1.5B |
|---|---|---|
| Dry bulk | Says no specialised equipment is needed; misleading | Correct core definition of unpackaged solid commodities |
| Ballast versus laden | Incorrectly describes ballast as cargo carriage | Adds unsupported stability/fuel claims and misses a reliable simple distinction |
| ETA and readiness | Correctly says ETA does not guarantee loading readiness; other details need review | Incorrectly says ETA proves destination readiness |
| Enough information to confirm fixture? | Generic clarification instead of explaining absent assignment evidence | Same unnecessary clarification |

Larger capacity improved one educational response but did not consistently establish
reliable shipping knowledge. No aggregate conversation accuracy is claimed.

## Raw intent failure inspection

1.5B few-shot: four cargo queries invent dataset `cargo_enquiries`, four description
queries choose `query` instead of expected `summarize`, the definition question
returns prose instead of an intent, and the unsupported crane query invents a
`fleet` dataset and certification filter. Thus the 44.4% exact score combines
semantic failures, schema failures and action-label mismatches.

3B few-shot: four DWT queries add an unrequested sum aggregation; four description
queries choose `qa` while attaching record filters (the service rejects that
combination); the final two cases return prose instead of JSON. Its greater schema
validity therefore does not guarantee executable or semantically correct requests.

## Completed 3B observations

All 10 turns completed without runtime errors. The first six computed data responses
match the smaller-model runs. Dry-bulk has a correct core definition. Ballast/laden
starts with the correct cargo/no-cargo distinction but wrongly claims all cargo
spaces are filled with ballast water and adds unsupported crew/stability claims.
ETA correctly distinguishes estimated arrival from loading readiness, but describes
laycan imprecisely as an assessment process. Fixture confirmation still receives
the same generic clarification. These results do not support unrestricted advice.

## Recommendation

Use 1.5B few-shot as the next experimental intent baseline: it achieves the same
8/18 strict matches as 3B few-shot with lower observed CPU latency and memory.
Keep 3B as a schema-adherence comparator (16/18 valid versus 12/18 for 1.5B).
Neither is approved for production or unattended freight decisions. Do not infer
that more parameters alone solve factual reliability.

Before QLoRA, distinguish output-shape errors from intent semantics and application
state. A subsequent controlled prompting/schema-constrained experiment may address
formatting without training; this iteration did not test that. If SFT is pursued,
use the development/training splits and new training examples, never these regression
or conversation assessment cases. Obtain domain-expert labels on a fresh held-out
set before claiming generalisation or improved commercial decision quality.
