from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


STATE_VERSION = 1


@dataclass
class GradeState:
    fingerprints: set[str]
    first_run: bool
    created_at: str | None = None


def load_state(path: Path) -> GradeState:
    if not path.exists():
        return GradeState(fingerprints=set(), first_run=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    return GradeState(
        fingerprints=set(data.get("fingerprints", [])),
        first_run=False,
        created_at=data.get("created_at"),
    )


def save_state(path: Path, fingerprints: set[str], created_at: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "version": STATE_VERSION,
        "created_at": created_at or now,
        "updated_at": now,
        "fingerprints": sorted(fingerprints),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
