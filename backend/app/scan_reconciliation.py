"""Pure helpers for comparing a completed chat scan with active targets.

The scan is an observation of Douyin's official conversation list.  This module
keeps the comparison independent from SQLAlchemy and the browser executor so
that the five reconciliation buckets can be tested without a live profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable


RECONCILIATION_CATEGORIES = (
    "changed",
    "no_spark",
    "new",
    "not_observed",
    "unchanged",
)


def empty_reconciliation() -> dict[str, Any]:
    """Return the safe, schema-stable empty value used by old/new scan tasks."""

    return {
        "counts": {category: 0 for category in RECONCILIATION_CATEGORIES},
        "entries": [],
    }


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _clean_days(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _state(
    *,
    days: Any = None,
    kind: Any = None,
    label: Any = None,
    scanned_at: Any = None,
) -> dict[str, Any]:
    """Normalize an official snapshot into the public reconciliation shape."""

    return {
        "days": _clean_days(days),
        "kind": _clean_text(kind),
        "label": _clean_text(label),
        "scannedAt": _iso(scanned_at),
    }


def _target_value(target: dict[str, Any], name: str) -> Any:
    """Accept the public snapshot keys and convenient snake_case test inputs."""

    snake_name = {
        "targetId": "target_id",
        "conversationType": "conversation_type",
        "platformUserId": "platform_user_id",
        "platformConversationId": "platform_conversation_id",
    }.get(name, name)
    return target.get(name, target.get(snake_name))


def target_snapshot(target: Any) -> dict[str, Any]:
    """Capture only the active-target data needed before a scan starts."""

    return {
        "targetId": target.id,
        "title": target.nickname_snapshot,
        "conversationType": target.conversation_type,
        "platformUserId": target.platform_user_id,
        "platformConversationId": target.platform_conversation_id,
        "previous": _state(
            days=target.spark_days,
            kind=target.spark_kind,
            label=target.spark_label,
            scanned_at=target.spark_scanned_at,
        ),
    }


def match_scan_entry_to_target(
    entry: dict[str, Any], targets: Iterable[dict[str, Any]]
) -> dict[str, Any] | None:
    """Match one scan row using stable identity first, then exact nickname.

    A stable identity mismatch blocks the nickname fallback for that target.  A
    nickname is only a strict equality fallback; no fuzzy or contains matching
    is allowed.  Group conversations use their conversation ID and never a
    user ID.
    """

    entry_user_id = _clean_text(entry.get("platform_user_id"))
    entry_conversation_id = _clean_text(entry.get("platform_conversation_id"))
    entry_type = entry.get("conversation_type")
    entry_title = _clean_text(entry.get("title"))
    targets_list = list(targets)

    if entry_conversation_id:
        for target in targets_list:
            if _target_value(target, "platformConversationId") == entry_conversation_id:
                return target
        # An unbound group target may still be safely connected by its exact
        # name so the scan can hydrate its conversation ID.  If another
        # same-named target already owns a different ID, keep it unresolved.

    if entry_user_id:
        for target in targets_list:
            if _target_value(target, "platformUserId") == entry_user_id:
                return target
        # A user ID is a stronger fact than a name.  Do not fall back to a
        # same-named target that already owns another stable user ID.
        if entry_title:
            for target in targets_list:
                if (
                    _target_value(target, "title") == entry_title
                    and _target_value(target, "platformUserId")
                ):
                    return None

    if not entry_title:
        return None

    for target in targets_list:
        if _target_value(target, "title") != entry_title:
            continue
        target_type = target.get("conversationType")
        if target_type is None:
            target_type = _target_value(target, "conversationType")
        if target_type and entry_type and target_type != entry_type:
            continue
        target_user_id = _target_value(target, "platformUserId")
        target_conversation_id = _target_value(target, "platformConversationId")
        if target_user_id and entry_user_id:
            # Mismatched stable user IDs were handled above; keep this guard
            # explicit for readability and future callers.
            if target_user_id != entry_user_id:
                continue
        if target_conversation_id and entry_conversation_id:
            if target_conversation_id != entry_conversation_id:
                continue
        if target_conversation_id and entry_conversation_id is None:
            # A saved group ID must be present in the scan before it can be
            # considered the same conversation; a same-named row is unsafe.
            continue
        return target
    return None


def _scan_label(entry: dict[str, Any]) -> str | None:
    label = _clean_text(entry.get("streak_label"))
    if label:
        return label
    if _clean_days(entry.get("streak_days")) is not None:
        return None
    preview = _clean_text(entry.get("preview"))
    if preview and "火花" in preview:
        return preview[:64]
    return "无火花"


def _has_official_spark(entry: dict[str, Any]) -> bool:
    return (
        _clean_days(entry.get("streak_days")) is not None
        or bool(_clean_text(entry.get("streak_label")))
        or "火花" in (_clean_text(entry.get("preview")) or "")
    )


def current_scan_state(entry: dict[str, Any]) -> dict[str, Any]:
    return _state(
        days=entry.get("streak_days"),
        kind=entry.get("streak_kind"),
        label=_scan_label(entry),
    )


def _comparison_state(state: dict[str, Any]) -> tuple[Any, ...]:
    # Scan timestamps describe observation time, not a status change.
    return (state.get("days"), state.get("kind"), state.get("label"))


def build_reconciliation(
    targets_before_scan: Iterable[dict[str, Any]], entries: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Build the five fixed buckets from a complete successful scan.

    ``targets_before_scan`` must contain only active targets captured before
    the browser scan.  Soft-deleted targets are intentionally omitted by the
    caller, so a matching scan row becomes ``new`` and can be re-imported.
    """

    targets = [target for target in targets_before_scan if isinstance(target, dict)]
    scan_entries = [entry for entry in entries if isinstance(entry, dict)]
    result = empty_reconciliation()
    result["schemaVersion"] = 1
    matched_ids: set[str] = set()

    for entry in scan_entries:
        matched = match_scan_entry_to_target(entry, targets)
        current = current_scan_state(entry)
        if matched is None:
            category = "new"
            previous = _state()
            target_id = None
            title = _clean_text(entry.get("title")) or "未命名会话"
            conversation_type = entry.get("conversation_type")
        else:
            target_id = _target_value(matched, "targetId")
            title = _target_value(matched, "title") or _clean_text(entry.get("title")) or "未命名会话"
            conversation_type = _target_value(matched, "conversationType") or entry.get("conversation_type")
            previous = _target_value(matched, "previous") or _state()
            # A target is considered observed only once, even if a browser
            # adapter accidentally returns a duplicate row.
            if target_id in matched_ids:
                continue
            matched_ids.add(target_id)
            if not _has_official_spark(entry):
                category = "no_spark"
            elif _comparison_state(previous) != _comparison_state(current):
                category = "changed"
            else:
                category = "unchanged"

        result["counts"][category] += 1
        result["entries"].append(
            {
                "targetId": target_id,
                "title": title,
                "conversationType": conversation_type,
                "category": category,
                "previous": previous,
                "current": current,
            }
        )

    for target in targets:
        target_id = target.get("targetId")
        if target_id in matched_ids:
            continue
        previous = target.get("previous") or _state()
        category = "not_observed"
        result["counts"][category] += 1
        result["entries"].append(
            {
                "targetId": target_id,
                "title": _target_value(target, "title") or "未命名目标",
                "conversationType": _target_value(target, "conversationType"),
                "category": category,
                "previous": previous,
                "current": _state(),
            }
        )

    return result


def parse_reconciliation(value: str | None) -> dict[str, Any]:
    """Read persisted JSON defensively; malformed/old values are empty."""

    import json

    if not value:
        return empty_reconciliation()
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return empty_reconciliation()
    if not isinstance(payload, dict):
        return empty_reconciliation()
    counts = payload.get("counts")
    entries = payload.get("entries")
    if not isinstance(counts, dict) or not isinstance(entries, list):
        return empty_reconciliation()
    normalized = empty_reconciliation()
    schema_version = payload.get("schemaVersion")
    if isinstance(schema_version, int) and schema_version >= 1:
        normalized["schemaVersion"] = schema_version
    for category in RECONCILIATION_CATEGORIES:
        count = counts.get(category)
        normalized["counts"][category] = count if isinstance(count, int) and count >= 0 else 0
    normalized["entries"] = [item for item in entries if isinstance(item, dict)]
    return normalized
