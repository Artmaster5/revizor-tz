# Brief: independent judge of a review report against an answer key

A tool reviewed a requirements document and wrote a report with findings. There is an answer key: a list of real defects of that document. A program has already compared the two mechanically (by line numbers and keywords). The mechanical comparison is crude in both directions, so a human-style judgement is needed for every item. You are that judge.

You have no stake in the result. A low number is exactly as useful as a high one. Do not round up.

Base folder: the repository root.

## Inputs (read only these)
- the document: `{DOC}`
- the answer key: `{KEY}` — `defects` (id, severity, title, anchors = verbatim places in the document, why) and `good` (places that are written well);
- the report in machine form: `{REPORT_JSON}` — made by `python eval/judge_input.py <report>.json -o <short>.json`: every open finding of the report (fid, severity, title, problem, recommendation, quotes with line numbers), the questions to the author and the list of strengths the report names;
- the mechanical comparison: `{SCORE_TXT}` — for each key defect: «найден» (strict), «место» (same lines, keywords did not match) or «НЕТ».

Do not read any other file. Never read `.env`.

## Part 1 — every key defect, one by one

For **each** defect of the key (all of them, including those the program marked as found) decide:

- `found` — a reader who sees only the tool's finding (title + problem + recommendation + quote) would understand **the same problem** the key describes, and would know where it is. The wording may differ. The place may be a different line if it is clearly the same defect.
- `partial` — the finding names only a part of a multi-part defect, or names the problem so vaguely that the author of the document would have to guess what exactly is wrong.
- `not_found` — nothing in the report tells the reader about this problem. A finding about the same line but about a different problem is `not_found`. A general remark such as «требование неполное» without the specific point is `not_found`.

A clarifying question in the report counts only as `partial` unless a finding states the problem too.

For each defect record: the ids of the findings you relied on, a short quote from the finding that carries the point (≤ 25 words), the tool's severity, and one sentence of reasoning. If your verdict differs from the program's mark, say why in that sentence.

## Part 2 — every finding that matches no key defect

For **every** open finding of any level (CRITICAL, MAJOR and MINOR) that you did not use as `found`/`partial` evidence in Part 1, check it against the document and decide (the report is read by people, so repeated and empty findings cost them time; count them honestly):
- `real_not_in_key` — a real defect of the document that the key simply does not list;
- `duplicate` — says the same thing about the same place as another finding of the report (name it); also mark as `duplicate` the weaker of two findings that you both used for one key defect in Part 1 when they say the same thing;
- `false_alarm` — the document is fine at this place, or the finding misreads it;
- `overrated` — a real but small point given too high a level.

Also list any finding of any level that sits on a `good` place of the key and criticises it. And check `strengths`: if the report praises a place that the key lists as a defect, record it under `praised_defects`.

## Part 3 — levels
For the key's CRITICAL defects: did the tool give CRITICAL to each? And the reverse: for each CRITICAL finding of the tool, is the level justified (a direct contradiction, a broken main flow, misuse of a protocol, loss of money or data, a missing mandatory part), or is it inflated?

## Output
1. `{OUT}.json`: `{"defects": [{"id", "key_severity", "origin", "program_mark", "verdict", "finding_ids", "tool_severity", "evidence", "reason"}], "extra_findings": [{"fid", "severity", "verdict", "duplicate_of", "reason"}], "good_place_hits": [...], "critical": {...}, "totals": {...}}`. `origin` is copied from the key when the field exists.
2. `{OUT}.md` in Russian, plain words: a totals table (found / partial / not_found — overall, by key severity, and separately for defects with `origin == "organisers"` if the key has that field); the count of false alarms, overrated and duplicates; the list of every case where you disagree with the program, with the reason; the list of `not_found` and `partial` defects with a one-line note on what exactly the report lacks.

Your final message: the totals and the lists of `not_found` and `partial` ids with their titles.
