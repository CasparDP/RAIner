# pi review (glm-5.2:cloud): shared student feedback rules

## Batch a

# Code Review
**Overall:** MINOR ISSUES

## Summary
This branch centralizes voice/tone/no-judgment rules for the five student-facing feedback modes into one `STUDENT_FEEDBACK_RULES` block injected by `_build_prompt()`, removing duplicated per-prompt copies and broadening the regex voice check to include `feedback_results`. The approach is sound and the tests are thoughtful (covering override dir, non-leak to grading/review). My main concerns are residual verdict/quality-rating wording that survives in the prompts, a test assertion that likely does not do what it appears to claim, and a couple of subtle inconsistencies between the shared block and mode-specific prompts.

## Category results

| # | Category | OK/WARN/FAIL | Findings |
|---|----------|--------------|----------|
| 1 | Correctness | WARN | `test_student_feedback_rules_reach_student_facing_modes_only` asserts `"verdict" not in resolve_prompt(mode).lower()` but `feedback_results.md` still contains "Consistency verdict"? Actually renamed — but `feedback_hyp_rd.md` retains "verdict" elsewhere; see issues. Also `feedback.md` output format unchanged. |
| 2 | Structure | OK | Centralization is clean; `STUDENT_FACING_MODES` tuple reused in `_build_prompt` and voice check. |
| 3 | Reproducibility | OK | No path/seed concerns in this diff. |
| 4 | Error handling | OK | No new I/O or failure paths. |
| 5 | Comments | OK | Class-level comments explain the why (supervisor-facing vs student-facing). |
| 6 | Style | OK | Consistent with existing conventions. |
| 7 | Performance | OK | String concat once per prompt build; negligible. |
| 8 | Security | OK | No secrets, no shell, no untrusted input handling changes. |

## Issues

1. **MINOR — `feedback_hyp_rd.md` still uses "verdict" in output A heading text indirectly.** Actually the A heading was rewritten to "Start with the most important gap…" — good. But `test_student_feedback_rules_reach_student_facing_modes_only` asserts `"verdict" not in resolve_prompt(mode).lower()` for *all* student-facing modes. Grep risk: `feedback.md` A heading now reads "the first bullet states the main data and feasibility finding" — OK. But `feedback_hyp_rd.md` line ~44 still says "Lead with a verdict on whether the empirical analyses actually test the stated hypotheses" — wait, the diff rewrites this to "Start with the most important gap…". Let me re-check: the diff replaces it. So OK. However, `feedback.md`'s OUTPUT FORMAT is not fully scanned: the removed `CLAUDE.md` text said "include feasibility verdict" → now "first bullet: main data and feasibility finding", but `feedback.md` itself line 31 changes to match. Consistent. **The real residual:** `feedback_hyp_rd.md` section A still contains "Flag the single most critical hypothesis–design gap if one exists." — that's fine (concrete gap). Withdraw concern; no FAIL. What would change my mind: a grep showing "verdict" remaining in any student-facing `.md` after applying this diff, which would make the test fail.

2. **MAJOR — Test assertion `"verdict" not in resolve_prompt(mode).lower()` is fragile and possibly false-passing.** `tests/test_prompts.py` line ~106: this asserts the *base prompt file* (before `STUDENT_FEEDBACK_RULES` is appended) contains no "verdict". But `feedback_results.md` retains the table column "Consistency verdict" — wait, the diff renames it to "Promised vs. delivered". Good. But does any student-facing prompt still contain "verdict"? `feedback_hyp_rd.md`: the diff rewrites A but I need to verify no other "verdict" survives. If the test currently passes, it means none do — but the assertion is checking the **resolve_prompt output only**, not the built prompt, so it does NOT verify the shared block is free of "verdict" (which would be fine since the shared block uses "NO-JUDGMENT RULE" and "grade" but not "verdict"). The assertion gives a false sense that verdict wording is banned from what the student sees. **What would change my mind:** either (a) rename the assertion to clarify it only checks base prompt files, or (b) assert against `ResearchAgent(...)._build_prompt()` for the full student-visible string, scoped to avoid the legitimate "NO-JUDGMENT RULE" mention.

3. **MAJOR — `STUDENT_FEEDBACK_RULES` rule 8 ("In a strengths section, name specific things…") may conflict with modes that lack a Strengths section or phrase it differently.** `feedback_idea.md` has no "Strengths" heading; `feedback_final.md` has B/C as Recommended/Optional changes with no strengths section; `feedback_results.md` has no strengths section. Rule 8 references "a strengths section" which only `feedback.md` and `feedback_hyp_rd.md` have. This is not harmful (it's conditional guidance), but a student-facing model could infer it should *create* a strengths section in modes that don't define one, distorting output structure. The constraint says headings must stay in lockstep with `_REQUIRED_HEADING_LAYOUTS`. **What would change my mind:** reword rule 8 to "When a section asks for strengths, name specific things…" rather than presupposing one exists; or confirm via a test that no student-facing prompt's required layouts include a strengths section except the two intended.

4. **MINOR — Shared block rule 6 lists "satisfactory, good, excellent, unsatisfactory" but `feedback_final.md` previously had a stricter/superset list including "acceptable", "strong", "weak", "solid".** The new shared list includes "strong", "weak", "solid", "adequate", "thin", "promising", "worth pursuing", "below standard" — a good superset. But "acceptable" (in the old `feedback_final`) is dropped from the banned list. A model could write "acceptable" since it's no longer explicitly banned. Low risk but a regression in coverage. **What would change my mind:** add "acceptable" to the shared banned list, or confirm the list is intentionally illustrative-not-exhaustive (rule 6 says "e.g." so it's illustrative — acceptable in practice, but worth a comment).

5. **MINOR — `feedback.md` A-heading change is broader than the diff's stated intent.** The diff changes `feedback.md` A from "include #1 feasibility verdict" to "the first bullet states the main data and feasibility finding: which data are VERIFIED / UNVERIFIED and what that means for the design". The constraint says "do not rewrite prompt substance or structure." This is a substantive rephrasing of what the executive summary's first bullet must contain. It's defensible (removes "verdict"), but it does prescribe content more tightly than before. Flagging for reviewer awareness; not a defect.

6. **MINOR — `test_voice_check_covers_student_facing_modes_only` now asserts *all* student-facing modes flag "The student uses…", but `STUDENT_FACING_MODES` includes `feedback_results` which previously was *excluded*.** This is the intended fix. However, the test does not assert a *negative* — that `grading` and `review` do NOT flag. The old test had `assert _check("feedback_results", text) == []` (a negative for that mode); the new test drops all negatives except `grading`. Adding `review` to the negative case would strengthen it. **What would change my mind:** add `assert _check("review", text) == []` alongside `grading`.

7. **MINOR — `test_student_feedback_rules_survive_prompt_dir_override` mutates global config via `set_config` inside a `TemporaryDirectory` context but restores in `finally`.** The `TemporaryDirectory()` is exited before `assert`, which is fine (dir cleaned after assertions only because `with` closes after `finally`? Actually `with TemporaryDirectory()` closes at end of `with` block, and `finally` runs inside it — OK). The pattern is correct but fragile; a fixture or `monkeypatch` would be cleaner. Not a defect.

8. **MINOR — Shared block uses "you may say whether the submission covers what this feedback stage asks for" (rule 9), but `feedback_idea.md` INSUFFICIENT path (not shown changed) and `feedback_results.md`/`feedback_hyp_rd.md` INSUFFICIENT paths were updated to "does not yet cover what this stage asks for."** Good alignment. But `feedback_idea.md`'s INSUFFICIENT phrasing wasn't touched in this diff — verify it already uses stage-completeness wording or will now conflict with rule 9. **What would change my mind:** confirm `feedback_idea.md`'s insufficient/short-submission path already aligns, or add it to this branch.

9. **MINOR — No test verifies the shared block appears exactly once and is *not duplicated* when a mode-specific prompt still contains residual no-judgment text.** The diff removes the old blocks from `feedback_final.md`, `feedback_idea.md`, `feedback_hyp_rd.md`, but `feedback.md` and `feedback_results.md` never had explicit NO-JUDGMENT blocks — they relied on implicit behavior. Now they get the shared block. The `count(...) == 1` test covers duplication, good. But there's no test that *mode-specific* no-judgment language was fully removed (e.g., grep for "NO-JUDGMENT RULE" across prompts should hit only the shared block). **What would change my mind:** add a grep-style test that no `rainer/prompts/*.md` contains "NO-JUDGMENT RULE" or "VOICE RULE" headers.

10. **MINOR — AI-voice / jargon check on the shared block itself.** The block is clear and direct, but rule 6's parenthetical "(e.g. "satisfactory", "good", "excellent", "unsatisfactory")" and rule 8's "cluster standard errors by firm, which fits your panel data" are appropriately plain. No jargon leaks. OK.

## Score
Start: 100
- #2 fragile test assertion: -5
- #3 strengths-section presupposition: -5
- #6 missing negative for `review`: -3
- #8 possible `feedback_idea` insufficient-path drift: -2
- #9 no grep test for residual per-prompt no-judgment blocks: -2
- #4 "acceptable" dropped from banned list: -1

**Final score: 82**

## Batch b

# Code Review
**Overall:** MINOR ISSUES

## Summary
Both prompts are well-structured with strong anti-hallucination controls, clear output formats, and consistent student-facing voice ("you/your" throughout). No verdict or quality-rating wording survives in the student-visible output sections. The main concerns are potential conflicts between the shared `STUDENT_FEEDBACK_RULES` block (not shown, appended at runtime) and the mode-specific "Strengths" sections, an asymmetry in self-verification and data-verification rigor between the two files, and unprotected risk of system jargon leaking into student-facing output.

## Category results

| # | Category | OK/WARN/FAIL | Findings |
|---|----------|-------------|----------|
| 1 | Correctness | OK | Logic flows correctly; submission classification in `feedback_hyp_rd.md` is well-specified with three paths; output headings are internally consistent within each file. |
| 2 | Structure | OK | Clear section organization; output formats are explicit; FULL/PARTIAL/INSUFFICIENT branching in `feedback_hyp_rd.md` is clean. |
| 3 | Reproducibility | WARN | `CITATION_RULES`, `DATA_SOURCE_RULES`, and `MATH FORMATTING RULES` are referenced but not defined in either file. If these are defined in the shared block or config, fine; if not, the LLM lacks specification. |
| 4 | Error handling | WARN | `feedback.md` has no submission-classification or insufficient-submission fallback (unlike `feedback_hyp_rd.md`). If a student submits only a research question to the `feedback` mode, the prompt will attempt to restate hypotheses, unit of analysis, etc. that may not exist, with no guardrail. |
| 5 | Comments | OK | Instruction rationale is generally clear (e.g., "Treat the year as a soft hint, not a hard filter" explains *why*). |
| 6 | Style | OK | Consistent formatting, heading conventions, and voice across both files. |
| 7 | Performance | WARN | `feedback_hyp_rd.md`'s self-verification pass (lines ~120-135) asks the LLM to re-review its own entire output for six criteria and "revise the affected passage before returning the final report." This is a full second-pass generation that roughly doubles output tokens for this mode. `feedback.md` has no equivalent. |
| 8 | Security | OK | No hardcoded secrets; tool-call instructions are explicit and scoped. |

## Issues

### 1. MAJOR — "Strengths" sections may conflict with shared block's no-judgment rule
**Files:** `feedback.md` section C ("Strengths"); `feedback_hyp_rd.md` section F ("Strengths") and INSUFFICIENT section E ("What you DID provide well").

The branch adds a shared `STUDENT_FEEDBACK_RULES` block with a no-judgment rule, then removes per-prompt voice/no-judgment copies. But both prompts still instruct the LLM to produce "Strengths" sections — inherently evaluative. If the shared block says "do not judge" or "do not evaluate quality," the LLM faces a direct contradiction: one instruction says don't judge, another says list strengths.

"What you DID provide well" in the INSUFFICIENT path is softer, but "Strengths" in the FULL/PARTIAL paths is a quality assessment.

**What would change my mind:** The shared block's no-judgment rule explicitly exempts or harmonizes with "Strengths" sections (e.g., "you may note what is working, but do not assign quality ratings or verdicts"). Alternatively, "Strengths" is reworded to "What's working in your design" or similar non-evaluative framing in both files.

---

### 2. MAJOR — Self-verification pass in `feedback_hyp_rd.md` may violate the "no new automatic tone check" constraint
**File:** `feedback_hyp_rd.md`, "SELF-VERIFICATION PASS" section (~lines 120-135).

The branch constraint says: "no new automatic tone check (false positives cost a full rewrite round)." The self-verification pass includes:
- **#1 Voice**: "Every sentence addressing the student uses 'you/your'…"
- **#2 Math formatting**: LaTeX compliance check
- A directive to "revise the affected passage before returning the final report"

This is effectively an in-prompt automatic tone/voice check that can trigger rewrites. If this section was added by the branch, it directly violates the constraint. If it pre-dates the branch, the concern is the asymmetry: `feedback.md` lacks it, so two student-facing modes have different self-correction rigor.

**What would change my mind:** Confirmation that this self-verification pass predates the branch (git blame), or that it was intentionally retained while the *system-level* automatic tone check was the thing excluded. If new, it should be removed or the voice check item (#1) should be stripped from it.

---

### 3. MINOR — System jargon in instruction sections with no anti-leakage instruction
**Files:** Both.

Both prompts contain visible section headers like "HALLUCINATION CONTROLS," "Submission Fidelity Rules (anti-hallucination)," and references to "CITATION_RULES," "DATA_SOURCE_RULES," and "MATH FORMATTING RULES." These are system-internal terminology. Neither prompt explicitly tells the LLM not to reference these rule names in its student-facing output. LLMs sometimes echo instruction-section headers into output, especially when the instruction section is large relative to the output spec.

**What would change my mind:** A line in either prompt such as "Do not reference these instruction sections or rule names in your output" or confirmation that the output format is rigid enough (via `_REQUIRED_HEADING_LAYOUTS` enforcement) that leakage is structurally prevented.

---

### 4. MINOR — Asymmetric data-verification rigor between the two modes
**Files:** `feedback.md` vs `feedback_hyp_rd.md`.

`feedback.md` has a "MANDATORY FIRST STEP (Data verification)" requiring `refresh_database_index` and `search_databases` calls before commenting on feasibility. `feedback_hyp_rd.md` section E ("Data–Hypothesis Feasibility Check") explicitly says "This is a lightweight check, not a full data audit. Flag problems, don't exhaustively verify every database."

A student who submits a full draft to `feedback` mode gets rigorous data verification; the same student submitting to `feedback_hyp_rd` gets a lightweight check. This is defensible if the modes have different purposes (general feedback vs. hypothesis/design focus), but it's undocumented and could surprise users.

**What would change my mind:** A comment or docstring explaining the intentional difference, or alignment of the two approaches.

---

### 5. MINOR — `feedback.md` lacks an INSUFFICIENT/PARTIAL fallback
**File:** `feedback.md`.

`feedback_hyp_rd.md` handles three submission types (FULL DRAFT, PARTIAL, INSUFFICIENT) with distinct output formats. `feedback.md` assumes a complete draft and immediately instructs the LLM to "Restate the research question, hypotheses, unit of analysis, geography, timeframe, main variables, and identification strategy." If the submission is incomplete, the LLM has no fallback and may hallucinate missing elements — exactly what the anti-hallucination rules are meant to prevent.

**What would change my mind:** Confirmation that the routing layer guarantees `feedback` mode is only invoked for complete drafts, or addition of a minimal guard clause ("If the submission lacks a research question or hypotheses, state what is missing and ask clarifying questions before proceeding").

---

### 6. MINOR — Self-verification pass references undefined rule sets
**File:** `feedback_hyp_rd.md`, self-verification item #2.

The check says: "per the MATH FORMATTING RULES (no spaces inside `$...$`, no `\( \)` or `\[ \]`, multi-letter names in `\text{}`)." This gives partial inline definitions, which is good. But it also says "per CITATION_RULES and DATA_SOURCE_RULES strictly" in the main hallucination controls section without defining them. If these are in the shared block, fine; if not, the LLM is told to follow rules it cannot see.

**What would change my mind:** Confirmation that `CITATION_RULES`, `DATA_SOURCE_RULES`, and `MATH FORMATTING RULES` are all defined in the shared block or a config file that is always prepended.

---

## Score

| Item | Deduction |
|------|-----------|
| #1 — Strengths/no-judgment conflict | -12 |
| #2 — Self-verification pass / tone-check constraint | -10 |
| #3 — Jargon leakage risk | -4 |
| #4 — Asymmetric data verification | -3 |
| #5 — No insufficient-submission fallback in feedback.md | -3 |
| #6 — Undefined rule references | -3 |
| **Final score** | **65** |

## Batch c

# Code Review
**Overall:** MAJOR ISSUES

## Summary

These are three well-structured LLM prompt templates for student-facing thesis feedback at different draft stages. The branch described in the constraints replaces per-prompt voice/no-judgment rules with a shared runtime-appended block, rephrases verdicts as concrete gaps, and extends the regex voice check to `feedback_results`. The prompts are largely clean of explicit verdict or quality-rating language, but one structural conflict remains: `feedback_results` retains a "Strengths" section that asks the model to tell the student what they are "doing well," which directly contradicts a no-judgment rule. A second, softer conflict exists in the PARTIAL path's "well-specified" framing. I also cannot verify the shared block's content, the grading-mode isolation, or test coverage from these files alone.

## Category results

| # | Category | OK/WARN/FAIL | Findings |
|---|----------|-------------|----------|
| 1 | Correctness | WARN | Strengths section (feedback_results §G) produces output that violates the stated no-judgment rule; PARTIAL §A "well-specified" framing is positive judgment. |
| 2 | Structure | OK | Headings are internally consistent within each file; sections flow logically. |
| 3 | Reproducibility | WARN | Cannot verify shared block is appended correctly for all 5 modes or excluded from grading; no visibility into `_REQUIRED_HEADING_LAYOUTS` alignment. |
| 4 | Error handling | OK | INSUFFICIENT and PARTIAL paths are well-defined fallback structures; stage-completeness framing is within allowed bounds. |
| 5 | Comments | OK | Meta-instructions are clear; STATED/INFERRED/NOT FOUND taxonomy is consistently defined. |
| 6 | Style | OK | Heading conventions are consistent across files; no naming deviations. |
| 7 | Performance | OK | N/A for prompt files. |
| 8 | Security | OK | No hardcoded secrets; tool-call references appear consistent. |

## Issues

### 1. MAJOR — Strengths section in feedback_results conflicts with no-judgment rule

**File:** `feedback_results.md`, Section G:
> **G. Strengths**
> What the student is doing well in the results section — be specific. Ground in draft passages (STATED).

The branch's stated goal is a shared no-judgment block that replaces per-prompt copies. "What the student is doing well" is positive judgment — the model will produce sentences like "You correctly interpret Table 2" or "Your specification choices are appropriate," which are verdicts on quality. This directly conflicts with a no-judgment rule that presumably prohibits telling the student their work is good or bad. `feedback_final` has no Strengths section and explicitly prohibits quality statements ("Never state that a part of the thesis is fine, adequate, or in order"), so the inconsistency is visible across modes.

**What would change my mind:** Either (a) rephrase §G as "Concrete elements that are working" with instructions to describe *what the element does* rather than *that it is good* (e.g., "Table 2 reports clustered SEs matching the methods description on p. 12" rather than "Your table is well-constructed"), or (b) show that the shared block explicitly permits fact-grounded positive observations and only prohibits unfounded praise — in which case §G is compatible but the wording "doing well" should still be softened to avoid priming the model toward evaluative output.

---

### 2. MINOR — "Well-specified" language in PARTIAL path

**File:** `feedback_results.md`, PARTIAL SUBMISSION, §A:
> Note what is well-specified and what still needs clarification before results can be produced.

"Well-specified" is a positive quality judgment. Under a strict no-judgment rule, the model might either suppress this half of the instruction (producing only gap-lists) or produce verdict-like statements. The companion instruction ("what still needs clarification") is gap-framed and fine.

**What would change my mind:** Rephrase to "Note which design elements are fully specified (name them) and which still need clarification" — factual completeness check, not quality rating.

---

### 3. MINOR — feedback_final retains mode-specific no-judgment rules that may be redundant with the shared block

**File:** `feedback_final.md`:
> Never state that a part of the thesis is fine, adequate, or in order: that is a judgment too.
> Do not give a writing-style rating.
> Do not rate quality or add qualifiers such as "thin" or "underspecified"; put problems in B or C.

If the shared block already contains a general no-judgment rule, these are redundant copies — the kind the branch intended to remove. If they are intentionally stricter (feedback_final prohibits even *positive* judgments that other modes might allow), they should be framed as a mode-specific tightening, not a standalone rule, to avoid the model treating them as the *only* no-judgment instruction and ignoring the shared block's broader tone rules.

**What would change my mind:** Either (a) confirm these were intentionally retained as mode-specific overrides and add a one-line anchor like "In addition to the student-feedback rules above, for this mode specifically: ...", or (b) confirm the shared block does not cover these specific prohibitions and these are not redundant.

---

### 4. MINOR — Cannot verify grading-mode isolation or test coverage from these files

The constraints state the shared block is "never for the supervisor-facing grading mode" and "the existing regex voice check now also covers feedback_results." Neither the shared block text, the `_build_prompt()` logic, the grading prompt, nor the test file is provided. I cannot confirm:
- The block is conditionally appended for exactly the 5 named modes and excluded for grading.
- The regex test asserts `feedback_results` is in scope.
- The regex test asserts the grading mode is *not* checked (or that the block is absent).
- `feedback_hyp_rd` and bare `feedback` modes (mentioned in constraints but not provided) are consistent with the shared block.

**What would change my mind:** A test file showing parametrized cases for all 5 student modes (block present, regex checked) and the grading mode (block absent, regex not checked), plus the `_build_prompt()` conditional.

---

### 5. MINOR — Elevated meta-language could bleed into student-facing output

**File:** `feedback_results.md`:
> The centerpiece. For EACH hypothesis...

"Centerpiece" is mildly elevated diction. While this is an instruction to the LLM, models sometimes mirror the register of their instructions in output. Under a plain-language voice rule, this is a minor priming risk. Not a functional problem.

**What would change my mind:** Evidence that the shared block's voice rules are strong enough to override register priming, or a simple rephrase to "For each hypothesis..." without the editorial framing.

---

## Score

Start: 100

- −10: Strengths section produces no-judgment-conflicting output (Issue 1, MAJOR)
- −3: "Well-specified" judgment language in PARTIAL path (Issue 2, MINOR)
- −2: Potentially redundant no-judgment copies in feedback_final (Issue 3, MINOR)
- −3: Cannot verify isolation or test coverage (Issue 4, MINOR)
- −1: Minor register priming risk (Issue 5, MINOR)

**Final score: 81**

