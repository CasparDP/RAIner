"""Student tracking database for longitudinal feedback management.

Tracks students, draft versions, and feedback runs over time.
Uses a separate DuckDB file (students.duckdb) from the papers database.
"""

from __future__ import annotations

import difflib
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel

from .config import get_config


class Student(BaseModel):
    """A registered student."""

    student_id: int
    name: str
    email: str | None = None
    cohort: str | None = None
    created_at: str | None = None


class Draft(BaseModel):
    """A draft version for a student."""

    draft_id: int
    student_id: int
    version: int
    filename: str
    content_hash: str
    token_count: int
    submitted_at: str | None = None
    loaded_at: str


class FeedbackRun(BaseModel):
    """A feedback run linked to a specific draft."""

    run_id: int
    draft_id: int
    mode: str
    provider: str | None = None
    model: str | None = None
    session_id: str | None = None
    feedback_text: str
    feedback_edited: str | None = None
    edited_at: str | None = None
    created_at: str


class VersionDiff(BaseModel):
    """Summary of changes between two draft versions."""

    student_name: str
    from_version: int
    to_version: int
    added_lines: int
    removed_lines: int
    changed_sections: list[str]
    diff_summary: str
    previous_feedback: str | None = None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS students (
    student_id INTEGER PRIMARY KEY DEFAULT nextval('student_id_seq'),
    name       VARCHAR NOT NULL,
    email      VARCHAR,
    cohort     VARCHAR,
    created_at TIMESTAMP DEFAULT current_timestamp,
    UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS drafts (
    draft_id     INTEGER PRIMARY KEY DEFAULT nextval('draft_id_seq'),
    student_id   INTEGER NOT NULL REFERENCES students(student_id),
    version      INTEGER NOT NULL,
    filename     VARCHAR NOT NULL,
    content_hash VARCHAR NOT NULL,
    raw_text     TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    submitted_at TIMESTAMP,
    loaded_at    TIMESTAMP DEFAULT current_timestamp,
    UNIQUE (student_id, version),
    UNIQUE (content_hash)
);

CREATE TABLE IF NOT EXISTS feedback_runs (
    run_id           INTEGER PRIMARY KEY DEFAULT nextval('feedback_run_id_seq'),
    draft_id         INTEGER NOT NULL REFERENCES drafts(draft_id),
    mode             VARCHAR NOT NULL,
    provider         VARCHAR,
    model            VARCHAR,
    session_id       VARCHAR,
    feedback_text    TEXT NOT NULL,
    feedback_edited  TEXT,
    edited_at        TIMESTAMP,
    created_at       TIMESTAMP DEFAULT current_timestamp
);
"""

SEQUENCES_SQL = """
CREATE SEQUENCE IF NOT EXISTS student_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS draft_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS feedback_run_id_seq START 1;
"""


def _content_hash(text: str) -> str:
    """SHA-256 hash of draft content for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class StudentDB:
    """Interface to the student tracking DuckDB database."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = get_config().data.students_db_path
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables and sequences if they don't exist."""
        self.conn.execute(SEQUENCES_SQL)
        self.conn.execute(SCHEMA_SQL)

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------
    # Students
    # ------------------------------------------------------------------

    def register_student(
        self,
        name: str,
        email: str | None = None,
        cohort: str | None = None,
    ) -> Student:
        """Register a new student or return existing one by name."""
        existing = self.get_student_by_name(name)
        if existing:
            # Update email/cohort if provided and different
            if email and email != existing.email:
                self.conn.execute(
                    "UPDATE students SET email = ? WHERE student_id = ?",
                    [email, existing.student_id],
                )
                existing.email = email
            if cohort and cohort != existing.cohort:
                self.conn.execute(
                    "UPDATE students SET cohort = ? WHERE student_id = ?",
                    [cohort, existing.student_id],
                )
                existing.cohort = cohort
            return existing

        result = self.conn.execute(
            """INSERT INTO students (name, email, cohort)
               VALUES (?, ?, ?)
               RETURNING student_id, name, email, cohort, created_at::VARCHAR""",
            [name, email, cohort],
        ).fetchone()
        return Student(
            student_id=result[0],
            name=result[1],
            email=result[2],
            cohort=result[3],
            created_at=result[4],
        )

    def get_student_by_name(self, name: str) -> Student | None:
        """Lookup student by name (case-insensitive)."""
        row = self.conn.execute(
            """SELECT student_id, name, email, cohort, created_at::VARCHAR
               FROM students WHERE lower(name) = lower(?)""",
            [name],
        ).fetchone()
        if not row:
            return None
        return Student(
            student_id=row[0], name=row[1], email=row[2],
            cohort=row[3], created_at=row[4],
        )

    def list_students(self, cohort: str | None = None) -> list[Student]:
        """List all students, optionally filtered by cohort."""
        if cohort:
            rows = self.conn.execute(
                """SELECT student_id, name, email, cohort, created_at::VARCHAR
                   FROM students WHERE cohort = ? ORDER BY name""",
                [cohort],
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT student_id, name, email, cohort, created_at::VARCHAR
                   FROM students ORDER BY name""",
            ).fetchall()
        return [
            Student(student_id=r[0], name=r[1], email=r[2], cohort=r[3], created_at=r[4])
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Drafts
    # ------------------------------------------------------------------

    def store_draft(
        self,
        student_id: int,
        content: str,
        filename: str,
        token_count: int,
        submitted_at: str | None = None,
    ) -> Draft:
        """Store a new draft version. Auto-increments version per student.

        Returns existing draft if content_hash matches (re-load detection).
        """
        chash = _content_hash(content)

        # Check for duplicate content
        existing = self.conn.execute(
            """SELECT draft_id, student_id, version, filename, content_hash,
                      token_count, submitted_at::VARCHAR, loaded_at::VARCHAR
               FROM drafts WHERE content_hash = ?""",
            [chash],
        ).fetchone()
        if existing:
            return Draft(
                draft_id=existing[0], student_id=existing[1], version=existing[2],
                filename=existing[3], content_hash=existing[4],
                token_count=existing[5], submitted_at=existing[6], loaded_at=existing[7],
            )

        # Get next version number for this student
        max_ver = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM drafts WHERE student_id = ?",
            [student_id],
        ).fetchone()[0]
        next_version = max_ver + 1

        row = self.conn.execute(
            """INSERT INTO drafts (student_id, version, filename, content_hash,
                                   raw_text, token_count, submitted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               RETURNING draft_id, student_id, version, filename, content_hash,
                         token_count, submitted_at::VARCHAR, loaded_at::VARCHAR""",
            [student_id, next_version, filename, chash, content, token_count,
             submitted_at],
        ).fetchone()
        return Draft(
            draft_id=row[0], student_id=row[1], version=row[2],
            filename=row[3], content_hash=row[4], token_count=row[5],
            submitted_at=row[6], loaded_at=row[7],
        )

    def get_latest_draft(self, student_id: int) -> Draft | None:
        """Get the most recent draft for a student."""
        row = self.conn.execute(
            """SELECT draft_id, student_id, version, filename, content_hash,
                      token_count, submitted_at::VARCHAR, loaded_at::VARCHAR
               FROM drafts WHERE student_id = ?
               ORDER BY version DESC LIMIT 1""",
            [student_id],
        ).fetchone()
        if not row:
            return None
        return Draft(
            draft_id=row[0], student_id=row[1], version=row[2],
            filename=row[3], content_hash=row[4], token_count=row[5],
            submitted_at=row[6], loaded_at=row[7],
        )

    def get_draft_content(self, draft_id: int) -> str | None:
        """Retrieve the raw text of a draft."""
        row = self.conn.execute(
            "SELECT raw_text FROM drafts WHERE draft_id = ?", [draft_id]
        ).fetchone()
        return row[0] if row else None

    def get_drafts_for_student(self, student_id: int) -> list[Draft]:
        """Get all draft versions for a student, ordered by version."""
        rows = self.conn.execute(
            """SELECT draft_id, student_id, version, filename, content_hash,
                      token_count, submitted_at::VARCHAR, loaded_at::VARCHAR
               FROM drafts WHERE student_id = ?
               ORDER BY version ASC""",
            [student_id],
        ).fetchall()
        return [
            Draft(
                draft_id=r[0], student_id=r[1], version=r[2],
                filename=r[3], content_hash=r[4], token_count=r[5],
                submitted_at=r[6], loaded_at=r[7],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def store_feedback(
        self,
        draft_id: int,
        mode: str,
        feedback_text: str,
        provider: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
    ) -> FeedbackRun:
        """Store an LLM-generated feedback run."""
        row = self.conn.execute(
            """INSERT INTO feedback_runs
                   (draft_id, mode, provider, model, session_id, feedback_text)
               VALUES (?, ?, ?, ?, ?, ?)
               RETURNING run_id, draft_id, mode, provider, model, session_id,
                         feedback_text, feedback_edited, edited_at::VARCHAR,
                         created_at::VARCHAR""",
            [draft_id, mode, provider, model, session_id, feedback_text],
        ).fetchone()
        return FeedbackRun(
            run_id=row[0], draft_id=row[1], mode=row[2], provider=row[3],
            model=row[4], session_id=row[5], feedback_text=row[6],
            feedback_edited=row[7], edited_at=row[8], created_at=row[9],
        )

    def store_edited_feedback(self, run_id: int, edited_text: str) -> None:
        """Import an edited feedback version for an existing run."""
        self.conn.execute(
            """UPDATE feedback_runs
               SET feedback_edited = ?, edited_at = current_timestamp
               WHERE run_id = ?""",
            [edited_text, run_id],
        )

    def get_feedback_for_draft(self, draft_id: int) -> list[FeedbackRun]:
        """Get all feedback runs for a draft."""
        rows = self.conn.execute(
            """SELECT run_id, draft_id, mode, provider, model, session_id,
                      feedback_text, feedback_edited, edited_at::VARCHAR,
                      created_at::VARCHAR
               FROM feedback_runs WHERE draft_id = ?
               ORDER BY created_at DESC""",
            [draft_id],
        ).fetchall()
        return [
            FeedbackRun(
                run_id=r[0], draft_id=r[1], mode=r[2], provider=r[3],
                model=r[4], session_id=r[5], feedback_text=r[6],
                feedback_edited=r[7], edited_at=r[8], created_at=r[9],
            )
            for r in rows
        ]

    def get_latest_feedback_for_student(self, student_id: int) -> FeedbackRun | None:
        """Get the most recent feedback run for a student (across all drafts)."""
        row = self.conn.execute(
            """SELECT f.run_id, f.draft_id, f.mode, f.provider, f.model,
                      f.session_id, f.feedback_text, f.feedback_edited,
                      f.edited_at::VARCHAR, f.created_at::VARCHAR
               FROM feedback_runs f
               JOIN drafts d ON f.draft_id = d.draft_id
               WHERE d.student_id = ?
               ORDER BY f.created_at DESC LIMIT 1""",
            [student_id],
        ).fetchone()
        if not row:
            return None
        return FeedbackRun(
            run_id=row[0], draft_id=row[1], mode=row[2], provider=row[3],
            model=row[4], session_id=row[5], feedback_text=row[6],
            feedback_edited=row[7], edited_at=row[8], created_at=row[9],
        )

    # ------------------------------------------------------------------
    # Diffs
    # ------------------------------------------------------------------

    def compute_version_diff(
        self, student_id: int, from_version: int, to_version: int
    ) -> VersionDiff | None:
        """Compute a diff summary between two draft versions."""
        from .chunking import extract_key_sections

        from_row = self.conn.execute(
            "SELECT raw_text FROM drafts WHERE student_id = ? AND version = ?",
            [student_id, from_version],
        ).fetchone()
        to_row = self.conn.execute(
            "SELECT raw_text FROM drafts WHERE student_id = ? AND version = ?",
            [student_id, to_version],
        ).fetchone()

        if not from_row or not to_row:
            return None

        old_text = from_row[0]
        new_text = to_row[0]
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()

        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

        # Detect which sections changed
        old_sections = set(extract_key_sections(old_text).keys())
        new_sections = set(extract_key_sections(new_text).keys())
        all_sections = old_sections | new_sections

        old_sec_content = extract_key_sections(old_text)
        new_sec_content = extract_key_sections(new_text)
        changed_sections = []
        for sec in sorted(all_sections):
            old_c = old_sec_content.get(sec, "")
            new_c = new_sec_content.get(sec, "")
            if old_c != new_c:
                changed_sections.append(sec)

        # Build human-readable summary
        summary_parts = []
        summary_parts.append(f"{added} lines added, {removed} lines removed")
        if changed_sections:
            summary_parts.append(f"Sections modified: {', '.join(changed_sections)}")
        if new_sections - old_sections:
            summary_parts.append(f"New sections: {', '.join(new_sections - old_sections)}")
        if old_sections - new_sections:
            summary_parts.append(f"Removed sections: {', '.join(old_sections - new_sections)}")

        # Get previous feedback
        prev_draft = self.conn.execute(
            "SELECT draft_id FROM drafts WHERE student_id = ? AND version = ?",
            [student_id, from_version],
        ).fetchone()
        previous_feedback = None
        if prev_draft:
            fb = self.get_feedback_for_draft(prev_draft[0])
            if fb:
                # Prefer edited version if available
                latest = fb[0]
                previous_feedback = latest.feedback_edited or latest.feedback_text

        # Get student name
        student = self.conn.execute(
            "SELECT name FROM students WHERE student_id = ?", [student_id]
        ).fetchone()

        return VersionDiff(
            student_name=student[0] if student else "Unknown",
            from_version=from_version,
            to_version=to_version,
            added_lines=added,
            removed_lines=removed,
            changed_sections=changed_sections,
            diff_summary="; ".join(summary_parts),
            previous_feedback=previous_feedback,
        )

    # ------------------------------------------------------------------
    # Progress overview (for dashboard)
    # ------------------------------------------------------------------

    def get_progress_overview(self) -> list[dict[str, Any]]:
        """Get a summary of all students' progress for dashboard use."""
        rows = self.conn.execute(
            """SELECT
                s.student_id,
                s.name,
                s.email,
                s.cohort,
                COUNT(DISTINCT d.draft_id) AS num_drafts,
                MAX(d.version) AS latest_version,
                MAX(d.loaded_at)::VARCHAR AS last_submission,
                COUNT(DISTINCT f.run_id) AS num_feedback_runs,
                MAX(f.created_at)::VARCHAR AS last_feedback
            FROM students s
            LEFT JOIN drafts d ON s.student_id = d.student_id
            LEFT JOIN feedback_runs f ON d.draft_id = f.draft_id
            GROUP BY s.student_id, s.name, s.email, s.cohort
            ORDER BY s.name"""
        ).fetchall()
        return [
            {
                "student_id": r[0],
                "name": r[1],
                "email": r[2],
                "cohort": r[3],
                "num_drafts": r[4],
                "latest_version": r[5],
                "last_submission": r[6],
                "num_feedback_runs": r[7],
                "last_feedback": r[8],
            }
            for r in rows
        ]
