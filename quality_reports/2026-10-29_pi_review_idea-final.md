# Code Review
**Overall:** MINOR ISSUES

## Summary
The diff adds a new `feedback_idea` mode and rewrites `feedback_final.md` to eliminate verdicts/grades. The wiring is mostly consistent across `agent.py`, `cli.py`, `batch.py`, and `prompts.py` — the new mode appears in every mode-list that matters. The biggest risks are (1) a regex in `_ITEM_CAPS` that can double-count bold + heading-anchored numbers and will occasionally trigger unnecessary LLM rounds, and (2) `feedback_final.md` still contains phrasing that a student could misread as a quality verdict despite the no-judgment rule. Neither is critical, but both warrant fixes.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | WARN | Regex overcounts; `_REQUIRED_HEADING_LAYOUTS` for `feedback_final` drops the comment explaining omitted sections; `feedback_idea` heading "D. Data check" vs prompt "D. Data check" matches but `_ITEM_CAPS` uses exact-case `find` on lowercased content — OK. |
| 2 | Structure | OK | New mode threaded consistently. |
| 3 | Reproducibility | OK | No paths/seeds affected. |
| 4 | Error handling | WARN | `_ITEM_CAPS` `find` returning -1 is handled, but regex false positives are silent. |
| 5 | Comments | OK | Comment on `_ITEM_CAPS` is adequate. |
| 6 | Style | OK | Consistent. |
| 7 | Performance | OK | Regex is cheap. |
| 8 | Security | OK | No secrets/shell. |

## Issues

1. **MAJOR — `_ITEM_CAPS` regex overcounts and triggers spurious LLM rounds.** Line in diff:
   ```python
   count = len(re.findall(r"^(?:#+\s*)?(?:\*\*)?\d+[.)]", section, flags=re.MULTILINE))
   ```
   This matches both `1.` and `**1.` and `### 1.`, but it does **not** require the bold to close or the number to be followed by a space. Consequences:
   - A line like `**1.** Foo` is counted once (correct).
   - But a reference list line `1. Smith (2023)...` inside section C ("Suggested reading") **also** counts — which is intended — yet a numbered sub-point written as `1)` under a bullet in section B also counts, contradicting the comment "indented sub-points don't count" (the regex does not check indentation).
   - A heading line `### B. Your next steps` itself: the `B` is not a digit, so OK — but `### 1.` prose inside B is counted even if it's a sub-heading.
   - False positives cost a full extra LLM round and, worse, the follow-up asks the model to "return the COMPLETE report," so every false positive is a latency + token hit.

   **What would change my mind:** Either (a) tighten the regex to require a non-digit boundary and optional markdown bold that must close (`r"^(?:#+\s*)?(?:\*\*)?\d+[.)]\s"` plus a check that the line is not indented more than a bullet, e.g. `re.match(r"^\S", line)`), or (b) add a unit test with the known false-positive cases (sub-points, reference lists under C) showing the count matches the intended top-level count. The comment "indented sub-points don't count" is currently a lie the code does not enforce.

2. **MAJOR — `feedback_final.md` still invites a quality verdict in places.** Despite the NO-JUDGMENT RULE, several lines read as assessments a student could screenshot against professors:
   - "Prefer the smallest realistic set of changes over a rewrite. Do not recommend a major redesign unless the argument of the thesis cannot hold without it." — "cannot hold without it" implies the thesis currently *does* hold, i.e. a positive quality judgment.
   - "Check whether the conclusions follow from the findings and do not overclaim." — if the model reports "conclusions follow from findings," that is a verdict on the results chain.
   - "Check whether the thesis reads like a coherent final thesis rather than a proposal plus a results dump." — "coherent final thesis" is a quality label.
   - "Aim your next steps at the elements the next feedback stage will evaluate" appears in `feedback_idea.md`, not final, but the same pattern: describing what the *next* stage evaluates implicitly frames the current submission as not-yet-there.

   **What would change my mind:** Reword the heuristics to ask for the *presence/specifics* of elements rather than a judgment ("Report whether the conclusions section restates each finding and the matching claim; if a claim extends beyond a finding, list it as a Recommended change" instead of "Check whether conclusions follow"). The current text tells the model to *evaluate* the chain, which is exactly what produces verdicts.

3. **MINOR — `feedback_final` required headings lost the explanatory comment.** The old block had `# E (optional improvements) is conditional in the prompt, so not required`. The new block has no comment, and the new heading list (A–H) matches the prompt, so this is fine — but the new prompt's section C "Optional improvements" is described as "Put as much as you can here" with no cap, while section B is capped at 7 in prose. `_ITEM_CAPS` does **not** enforce the 7-cap on `feedback_final` (only `feedback_idea` is in `_ITEM_CAPS`). If the max-7 rule for Recommended Changes is meant to be enforced, it is not. If it's prompt-only, fine, but the asymmetry (idea mode enforced, final mode not) should be a deliberate choice documented somewhere.

4. **MINOR — `feedback_idea` prompt says "Between 5 and 10 numbered items, never more than 10" but `_ITEM_CAPS` only enforces the >10 cap.** A count of 3 items would not trigger anything. If "between 5 and 10" is a hard rule, the validator is incomplete. If 5 is a soft target, the prompt should say "aim for 5–10."

5. **MINOR — `journal_name` field added to search results without a schema check.** `r.paper.journal_name` is added to the dict at agent.py:679. If `paper.journal_name` can be `None`, the prompt instruction "The journal field in search results tells you where a paper appeared" will mislead the model when the field is blank. Worth either filtering `None` to `"Unknown"` or telling the prompt to treat absence as unknown.

6. **MINOR — `feedback_idea` prompt inconsistency on paper count.** "Up to 10 papers" and "Fewer than 10 is fine" but `_ITEM_CAPS` caps C at 10. Consistent, but the prompt also says "Never list a paper from memory" — good. No issue, just confirming alignment.

7. **MINOR — `/mode` acceptance list in cli.py:412 omits `exam-review`.** The choices list at line 280 includes `exam-review`, but the `/mode` handler at line 412 does not. This is pre-existing (not introduced by this diff), but since the diff touched this exact line to add `feedback_idea`, it's worth flagging that the same line is missing `exam-review`, meaning `/mode exam-review` silently falls to the else branch. Not this diff's bug, but adjacent.

## Score
Start: 100
- Regex overcounts / spurious LLM rounds: -8
- Final prompt still contains verdict-inviting phrasing: -8
- 5–10 lower bound not enforced / cap asymmetry undocumented: -3
- `journal_name` None handling: -2
- Adjacent `exam-review` omission on touched line: -2
**Final: 77**
