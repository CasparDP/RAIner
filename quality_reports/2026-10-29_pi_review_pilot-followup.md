# Code Review
**Overall:** MINOR ISSUES

## Summary
This diff adds pandoc-safe math formatting rules to system prompts, extends formatting checks to two additional modes, configures SDK retry behavior for rate limits, and disables OCR in PDF parsing. The regex work is thoughtful—false-positive avoidance for dollar amounts is handled well—but the OCR change silently degrades on scanned documents with no detection or fallback, and a couple of regex edge cases produce false negatives. Tests are reasonable but don't cover all four SDK providers.

## Category results

| # | Category | Result | Findings |
|---|----------|--------|----------|
| 1 | Correctness | WARN | Padded-math regex false-negatives when inline math is immediately followed by a digit; `\( \[` check runs on full content including code blocks |
| 2 | Structure | OK | MATH_RULES as class constant is clean; mode gating in `_check_formatting_issues` is well-structured |
| 3 | Reproducibility | WARN | `LLM_MAX_RETRIES` is a magic constant with a good comment but not configurable; no test for non-Azure providers |
| 4 | Error handling | FAIL | `do_ocr=False` in `parse_pdf` silently returns empty/near-empty text for scanned PDFs—no detection, no warning, no fallback |
| 5 | Comments | OK | Retry rationale and pandoc rendering context are well-documented |
| 6 | Style | OK | Consistent with existing patterns; raw strings correctly used for LaTeX |
| 7 | Performance | OK | OCR removal reduces memory/time for born-digital PDFs; regex passes are linear |
| 8 | Security | OK | Test uses dummy API key; no secrets introduced |

## Issues

1. **MAJOR — `do_ocr=False` silently fails on scanned PDFs** (`rainer/pdf.py:83-90`)
   The comment says "Theses are born-digital: OCR adds memory and time but no text." But if any user uploads a scanned thesis (photocopy, handwritten sections, image-only PDF), `export_to_markdown()` will return empty or garbage text with no error. An embedding caller using `FeedbackEngine` would get a silently degraded result—no exception, no warning. The function already returns `None, {}` for other failure cases (line 80), so at minimum the caller should be alerted when extraction yields suspiciously little text.
   **What would change my mind:** Evidence that all inputs are guaranteed born-digital (e.g., an upstream validation step), or that `parse_pdf` post-checks output length and logs/raises when the extracted text is empty or near-empty after disabling OCR.

2. **MINOR — Padded-math regex false negative when math is followed by a digit** (`rainer/agent.py:1377`)
   The regex `(?<![\\$])\$([^$\n]+?)\$(?![$\d])` uses negative lookahead `(?![\d])` to skip dollar amounts like `$5`. But this also skips legitimate padded inline math that happens to be followed by a digit, e.g., `$ \beta $2x` — the closing `$` is followed by `2`, so the lookahead rejects the match and the violation goes uncaught. This is an unusual construction in feedback reports, but it's a gap in coverage. Consider separating the "is this a dollar amount" heuristic from the "is this math" heuristic, e.g., by only applying the digit lookahead when the captured content lacks math characters.

3. **MINOR — `\( \[` delimiter check ignores code blocks** (`rainer/agent.py:1365`)
   `re.search(r"\\[(\[]", content)` runs on the full content including fenced code blocks. If the LLM writes a code sample containing `\(` or `\[` (e.g., explaining LaTeX syntax), this triggers a false positive and costs an extra full-draft LLM round-trip. The existing Unicode math check strips LaTeX regions first; the new checks should similarly skip code fences. The pre-existing Unicode check at line ~1355 already has this concern, but adding two more unchecked passes compounds it.

4. **MINOR — Retry test only covers Azure** (`tests/test_formatting_checks.py:83-91`)
   `test_azure_client_retries_rate_limits` parametrizes over `chat`/`responses` but doesn't verify `max_retries` on the OpenAI, OpenRouter, or Anthropic clients. The diff adds the parameter to all four constructors, but a regression in any non-Azure path would go undetected. Adding analogous assertions (or at minimum one per provider) would close the gap.

5. **MINOR — MATH_RULES appended to every mode including `search`** (`rainer/agent.py:875-882`)
   `_build_system_prompt` unconditionally appends `MATH_RULES` regardless of mode. For `search` mode (which retrieves papers, not writes reports), these rules add token overhead with no benefit. Not harmful, but if `search`-mode prompts are token-constrained, this wastes budget. If this is intentional (e.g., for consistency), a one-line comment would help.

6. **MINOR — `LLM_MAX_RETRIES=8` is not configurable** (`rainer/agent.py:27`)
   With 8 retries and retry-after up to 60s, a single rate-limited call can block for up to ~8 minutes. This is a deliberate tradeoff, but embedding callers cannot tune it without monkey-patching the constant. Consider reading from `self.config` or exposing it as a parameter.

## Score
- Start: 100
- −12: MAJOR #1 (silent OCR failure, no detection)
- −3: MINOR #2 (regex false negative)
- −3: MINOR #3 (code-block false positives)
- −2: MINOR #4 (incomplete retry test coverage)
- −2: MINOR #5 (unnecessary MATH_RULES in search mode)
- −1: MINOR #6 (non-configurable retry constant)
- **Final: 77**
