from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Grade:
    term: str
    course_name: str
    teacher: str
    course_type: str
    course_module: str
    credit: str
    final_score: str
    grade_point: str
    mark: str

    @property
    def display_title(self) -> str:
        return self.course_name or "未知课程"

    def display_line(self) -> str:
        parts = [self.display_title]
        if self.final_score:
            parts.append(f"成绩 {self.final_score}")
        if self.grade_point:
            parts.append(f"绩点 {self.grade_point}")
        if self.credit:
            parts.append(f"{self.credit} 学分")
        if self.term:
            parts.append(self.term)
        return "，".join(parts)

    def fingerprint(self, salt: str = "") -> str:
        raw = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(f"{salt}:{raw}".encode("utf-8")).hexdigest()


def normalize_grade_rows(rows: Iterable[dict[str, Any]]) -> list[Grade]:
    grades: list[Grade] = []
    for row in rows:
        course_name = _text(row, "kcname")
        final_score = _text(row, "zcjname1")
        if not course_name or not final_score:
            continue
        grades.append(
            Grade(
                term=_first_text(row, ["xnxq", "xnxqname", "jczy013id_name", "jczy013id"]),
                course_name=course_name,
                teacher=_text(row, "jsname"),
                course_type=_text(row, "kclbname"),
                course_module=_text(row, "kcmk"),
                credit=_text(row, "xf"),
                final_score=final_score,
                grade_point=_text(row, "jd"),
                mark=_text(row, "cjbzname"),
            )
        )
    return grades


def find_new_grades(
    grades: Iterable[Grade],
    seen_fingerprints: set[str],
    salt: str = "",
) -> list[Grade]:
    return [
        grade
        for grade in grades
        if grade.fingerprint(salt) not in seen_fingerprints
    ]


def fingerprint_set(grades: Iterable[Grade], salt: str = "") -> set[str]:
    return {grade.fingerprint(salt) for grade in grades}


def _text(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _first_text(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = _text(row, key)
        if value:
            return value
    return ""
