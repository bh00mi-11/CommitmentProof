"""Evidence-based commitment gate. Local policy only, NOT a Switch agent."""
from __future__ import annotations
import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class Assessment:
    decision: str
    reasons: tuple[str, ...]
    can_claim_complete: bool
    permitted_action: str
    def to_dict(self) -> dict[str, Any]:
        return {"decision": self.decision, "reasons": list(self.reasons), "can_claim_complete": self.can_claim_complete, "permitted_action": self.permitted_action}

def assess(evidence: dict[str, Any]) -> Assessment:
    """Unknown values fail closed. Review eligibility is NOT completion."""
    if not isinstance(evidence, dict):
        raise TypeError("evidence must be a mapping")
    reasons: list[str] = []
    blocked = False
    for name, description in (
        ("pr_merged", "pull request merged"),
        ("tests_passed", "required tests passed"),
        ("deployment_verified", "deployment verified"),
        ("qa_confirmed", "QA confirmation"),
    ):
        entry = evidence.get(name)
        if not isinstance(entry, dict):
            reasons.append(f"No evidence for {description}")
            continue
        value = entry.get("value")
        source = entry.get("source_url")
        if value is False:
            blocked = True
            reasons.append(f"Negative evidence: {description}")
        elif value is not True:
            reasons.append(f"Unresolved: {description}")
        elif not isinstance(source, str) or not source.startswith("https://"):
            reasons.append(f"Source link missing for {description}")
    if blocked:
        return Assessment("BLOCKED", tuple(reasons), False, "PREPARE_FOLLOWUP_DRAFT")
    if reasons:
        return Assessment("NEEDS_EVIDENCE", tuple(reasons), False, "REQUEST_EVIDENCE")
    return Assessment("REVIEW_READY", ("All required checks have source links; human approval still required",), False, "REQUEST_HUMAN_APPROVAL")

def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect source-linked customer commitment evidence")
    parser.add_argument("evidence", type=Path, help="JSON evidence file")
    args = parser.parse_args()
    data = json.loads(args.evidence.read_text(encoding="utf-8"))
    print(json.dumps(assess(data).to_dict(), indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
