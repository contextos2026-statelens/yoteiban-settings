"""案件を必要な思考量に応じた4段へ振り分ける。"""
from __future__ import annotations

LEVELS = ("すぐ", "簡単", "難しい", "もっと難しい")


def _truthy(data, *keys):
    return any(bool(data.get(key)) for key in keys)


def _items(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def route(case, signals, settings=None) -> dict:
    """返事・単純な空き探し・複雑な調整・長い調査を4段で分類する。"""
    case = case if isinstance(case, dict) else {}
    settings = settings or {}
    data = signals if isinstance(signals, dict) else {}
    text_parts = [str(value) for value in _items(signals) if isinstance(value, str)]
    for key in ("kind", "type", "intent", "description", "task"):
        if data.get(key):
            text_parts.append(str(data[key]))
    for key in ("title", "summary", "kind", "description"):
        if case.get(key):
            text_parts.append(str(case[key]))
    text = " ".join(text_parts)
    lower = text.lower()

    # 設定で複雑さの境目を調整できる。
    compare_threshold = max(2, int(settings.get("comparison_threshold", 3)))
    condition_threshold = max(2, int(settings.get("complex_condition_threshold", 2)))
    research_days = max(1, int(settings.get("more_days_threshold", 2)))

    people = data.get("people", data.get("participants", data.get("person_ids", [])))
    people_count = len(_items(people)) if people else int(data.get("people_count", 0) or 0)
    conditions = data.get("conditions", data.get("constraints", []))
    condition_count = len(_items(conditions)) if conditions else int(data.get("condition_count", 0) or 0)
    comparisons = data.get("comparison_count", data.get("options_count", 0)) or 0
    try:
        comparisons = int(comparisons)
    except (TypeError, ValueError):
        comparisons = 0
    duration_days = data.get("research_days", data.get("plan_days", data.get("duration_days", 0))) or 0
    try:
        duration_days = int(duration_days)
    except (TypeError, ValueError):
        duration_days = 0

    quick = _truthy(data, "reply_only", "writing_only", "simple_reply", "compose_only") or any(
        word in lower for word in ("返事だけ", "返信だけ", "作文だけ", "文案だけ")
    )
    simple = _truthy(data, "one_slot_search", "single_person", "one_person", "one_free_slot") or any(
        word in lower for word in ("1件の空き", "一人の空き", "1人の空き", "一枠")
    ) or people_count == 1 or (0 < comparisons < compare_threshold) or (0 < duration_days < research_days)
    complex_case = (
        people_count > 1
        or _truthy(data, "multiple_people", "multiple_conditions", "travel", "family_calendar", "family_related")
        or condition_count >= condition_threshold
        or any(word in lower for word in ("複数人", "複数条件", "移動あり", "家族の予定"))
    )
    hard = (
        duration_days >= research_days
        or comparisons >= compare_threshold
        or _truthy(data, "long_research", "multi_day_plan", "many_comparisons")
        or any(word in lower for word in ("数日以上", "数日間", "下調べ", "比較", "比べる", "計画を立てる"))
    )
    uncertain = _truthy(data, "uncertain", "ambiguous", "low_confidence") or not (quick or simple or complex_case or hard) or any(
        word in lower for word in ("不明", "あいまい", "曖昧", "自信がない", "条件未確認")
    )

    level = 0 if quick else 1
    why = "返事や文案だけなので、短く処理できます" if quick else "1件の予定調整として扱います"
    if simple and not quick:
        level = max(level, 1)
        why = "1人分の空き探しなので、条件を絞って確認します"
    if complex_case:
        level = max(level, 2)
        why = "複数人・複数条件・移動・家族予定を含むため、順に照合します"
    if hard:
        level = 3
        why = "数日にわたる調査・計画、または多くの候補比較が必要です"
    if uncertain and level < 3:
        level += 1
        why += "。不明点があるため、1段上で慎重に扱います"
    return {"level": LEVELS[level], "why": why}
