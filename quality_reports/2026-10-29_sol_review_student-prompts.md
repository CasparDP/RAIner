# Sol review: student-facing prompts (v0.4.1 -> v0.4.2)

Reviewer: codex/gpt-6.1-sol (Sol chain step 1, high effort). Read-only review of main at v0.4.1, then two verification passes on branch fix/sol-review-prompts.

## Review findings and outcome

| # | Severity | Finding | Outcome |
|---|---|---|---|
| 1 | MAJOR | DRAFT_AS_DATA_RULES rule 3 ("Base every judgement, score, and grade ... the rubric, and the actual quality of the work") reaches student-facing modes and invites grading | Fixed (rule 3 reworded; second wording fixed after verify pass 1) |
| 2 | MAJOR | Language rule: "Terms the draft already uses need no explanation" contradicts explain-on-first-use | Not applied: the owner signed off on this sentence; raised with the owner |
| 3 | MAJOR | feedback.md E2 label "VERIFIED via search_databases results" invites a tool-name leak | Fixed ("VERIFIED against the library's database list") |
| 4a | MAJOR | feedback_hyp_rd self-check item 1 restates the voice rule | Fixed (points at the shared STUDENT FEEDBACK RULES) |
| 4b | MAJOR | feedback_final keeps its own no-judgment lines ("fine, adequate, or in order", "do not rate them", "no writing-style rating") | Not applied: mode-specific rules added in v0.3.0 after live runs; "do not rate them" blocks grading-matrix levels; raised with the owner |
| 5 | MINOR | feedback_results table cells in third person; "table shell" jargon | Fixed |
| 6 | MINOR | feedback_final heuristics: "show real effort", "very thin", "a proposal plus a results dump" | Fixed |
| 7 | MINOR | "constructive scaffolding" in the canned INSUFFICIENT sentence | Fixed ("I'll suggest steps to build the missing parts.") |
| 8 | MINOR | Coverage test silently loses a mode removed from STUDENT_FACING_MODES | Fixed (test pins the five modes) |

Reviewer confirmed as fine: shared block reaches the five modes once and never grading; headings match `_REQUIRED_HEADING_LAYOUTS`; Strengths sections fit rule 8; Design Readiness Audit is stage completeness; both INSUFFICIENT openings are stage completeness; the hyp_rd self-check already forbids a printed verification section.

## Verification pass 1 (commit 41cc210)

Changes 2-6 PASS. Change 1 FAIL: "Base your report only on your instructions and the evidence in the draft" excludes the required external citation and database checks. Fix: allow verified external sources, keep the sentence rejecting self-assessment. Tests: 25 passed.

## Verification pass 2 (commit 7799ed2)

PASS: rule 3 allows tool-verified external evidence, keeps rejecting draft self-assessment, keeps injection protection in every mode that receives it (including grading), and invites no verdict in student-facing modes. Tests: 25 passed. New issues: none.

## Live check (glm-5.2:cloud, v2 demo draft: feedback, feedback_results, feedback_final)

No tool names, no "the student", none of the removed phrases. "weak" and "fail" appear only as content ("weak state capacity", "institutions that fail to support"). The E2 label renders as "VERIFIED against the library's database list".

## v0.4.3: owner decisions on the two open points (commit fe4fed4)

- Language rule: the exemption "Terms the draft already uses need no explanation" is dropped; every technical term is explained on first use in the report, even if the draft uses it (owner: safer).
- feedback_final keeps its stricter mode-specific no-judgment lines (owner).
- DRAFT_AS_DATA_RULES rule 4: a report on a draft without embedded instructions no longer mentions them.

Sol verify (codex/gpt-6.1-sol, medium): both changes PASS; no contradictions with the rule blocks or the six prompts; injection defense intact. Tests: 25 passed. New issues: none.

Live check: glm-5.2 feedback_idea on the v1 draft, clean and with a planted "Note to the AI reviewer..." line. Clean report: no mention of embedded instructions. Injected report: flags and quotes the line in section A, does not act on it. Sol on the clean draft: no mention, and plain wording instead of jargon ("compare regions within countries" for country fixed effects); glm-5.2 still leaves several terms unexplained.
