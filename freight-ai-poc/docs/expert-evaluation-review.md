# Pending freight-expert review

Candidate questions and rubrics: data/evaluation/expert_review_queue.json.
These are fresh assistant-authored development candidates, not expert-reviewed and
not a certified blind test. Do not report benchmark accuracy from this queue.

A freight expert must inspect each case against the frozen workbooks, correct the
rubric, specify exact expected record IDs/numbers where applicable, and mark each
case approved or rejected. Record reviewer identity, date and rationale. Rate
answers separately for factual accuracy, intent/constraint retention, source
attribution, uncertainty handling and readability (0 incorrect, 1 partial, 2 meets
rubric). A fluent incorrect answer fails factual accuracy.

After review, freeze a version and SHA-256 digest. Keep approved test cases outside
training and prompt demonstrations. Because developers can see these candidates,
ask the expert to supply additional unseen paraphrases and cases for the final
blind holdout. Do not iterate prompts against that final holdout.
