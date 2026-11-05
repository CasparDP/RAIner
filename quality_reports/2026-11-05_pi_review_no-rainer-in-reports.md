# Code Review
**Overall:** SOUND

## Summary
This two-repo change removes the engine's stylized name ("RAiner"/"RAINER") from student- and supervisor-facing outputs. Rainer adds a formatting check that flags the name in generated reports and updates its prompt templates to use "VERIFIED" / "NOT IN PAPER DATABASE" labels. Mattie's renderer adds a badge kind for the new label and strips the legacy " IN RAINER" suffix from old output for backward compatibility. The implementation is careful: regex alternation ordering is correct, backward compatibility is preserved, and tests cover both new and legacy paths.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | OK | Regex alternation order correctly prioritizes `NOT FOUND` before `NOT IN PAPER DATABASE`; both match their intended targets. Badge label lookup after suffix stripping always finds a key in `_LABEL_KINDS`. |
| 2 | Structure | OK | Changes are localized and follow existing patterns. |
| 3 | Reproducibility | OK | Version bump is consistent across `pyproject.toml` and `__init__.py`. |
| 4 | Error handling | OK | `_LABEL_KINDS[label]` could raise `KeyError` on an unexpected label, but the regex alternatives map 1:1 to dict keys, so this is unreachable in practice. |
| 5 | Comments | WARN | Comment in `agent.py` line ~1461 ("Its spellings only, so a cited author called Rainer does not trigger it.") is slightly unclear on first read—could state "the engine's stylized spellings" explicitly. |
| 6 | Style | OK | Consistent with surrounding code. |
| 7 | Performance | OK | Single `re.search` per report; no concern. |
| 8 | Security | OK | No secrets, no shell commands, no input validation concerns. |

## Issues

1. **MINOR — agent.py:1461** — The regex `\b(?:RAiner|RAINER)\b` catches two specific casings but not lowercase "rainer". This is likely intentional (a lowercase "rainer" in a formal report is improbable, and "Rainer" with normal capitalization must be allowed as a possible author surname). If the LLM ever writes "the rainer database" in lowercase, it would slip through. *What would change my mind:* confirmation that the LLM is reliably instructed to never reference the engine by any casing, or that lowercase usage is acceptably caught by downstream review.

2. **MINOR — render.py:340** — `kind = _LABEL_KINDS[label]` uses direct dict indexing. If a future label is added to the regex but not to `_LABEL_KINDS`, this raises `KeyError` at render time. A `.get(label, "neutral")` with a log warning would be more defensive, though current code is safe since the two structures are in sync.

3. **MINOR — test_render.py:197** — The existing test `test_label_badges_replace_labels_and_drop_their_wrappers` lost its assertion for `#badge("ok", "VERIFIED IN RAINER")` (replaced by the "NOT IN PAPER DATABASE" case). The new test `test_label_badges_drop_the_old_engine_name` covers "VERIFIED IN RAINER" → "VERIFIED", so coverage is preserved, but the split makes the backward-compat guarantee slightly less visible in the primary test.

## Score
- Start: 100
- Comment clarity (issue 5): −2
- Defensive lookup in `_badge` (issue 2): −2
- **Final: 96**
