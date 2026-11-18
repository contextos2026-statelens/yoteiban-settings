"""架空 fixture と読み取り結果を比較する採点関数。"""
from __future__ import annotations


def _title_similarity(a: str, b: str) -> float:
    def grams(text: str) -> set[str]:
        text = ''.join(text.split())
        if len(text) < 3:
            return {text} if text else set()
        return {text[i:i + 3] for i in range(len(text) - 2)}
    left, right = grams(a), grams(b)
    return len(left & right) / len(left | right) if left | right else 1.0


def _same(expected: dict, actual: dict) -> bool:
    if expected.get('kind') != actual.get('kind'):
        return False
    es, ac = expected.get('event_start'), actual.get('event_start')
    if (es or '')[:10] != (ac or '')[:10]:
        return False
    return _title_similarity(str(expected.get('title', '')), str(actual.get('title', ''))) >= 0.5


def score_reading(expected: list[dict], actual: list[dict]) -> dict:
    """対応づけを貪欲に行い、件数ベースの適合率・再現率を返す。"""
    exp_by_id = {x['input_id']: x for x in expected}
    act_by_id = {x['input_id']: x for x in actual}
    missed = false = total = matched = rel_correct = 0
    for iid, exp in exp_by_id.items():
        act = act_by_id.get(iid, {})
        expected_items = exp.get('items', [])
        actual_items = list(act.get('items', []))
        total += len(expected_items)
        for item in expected_items:
            idx = next((n for n, candidate in enumerate(actual_items) if _same(item, candidate)), None)
            if idx is None:
                missed += 1
            else:
                actual_items.pop(idx); matched += 1
        false += len(actual_items)
        rel_correct += int(bool(exp.get('relevant')) == bool(act.get('relevant', bool(act.get('items')))))
    denom = matched + missed + false
    accuracy = matched / denom if denom else 1.0
    return {'accuracy': accuracy, 'missed': missed, 'false': false,
            'relevance_accuracy': rel_correct / len(exp_by_id) if exp_by_id else 1.0}


def score_outlook(expected: list[dict], actual: list[dict]) -> dict:
    """場面単位の Finding 再現率と誤検知数を返す。"""
    exp_map = {x['scene_id']: {(f.get('type'), f.get('date')) for f in x.get('expected', [])} for x in expected}
    act_map = {x['scene_id']: {(f.get('type'), f.get('date')) for f in x.get('findings', [])} for x in actual}
    total = found = false_count = 0
    false_per_scene = {}
    for sid, wanted in exp_map.items():
        got = act_map.get(sid, set())
        total += len(wanted); found += len(wanted & got)
        extra = len(got - wanted); false_count += extra; false_per_scene[sid] = extra
    recall = found / total if total else (1.0 if false_count == 0 else 0.0)
    return {'recall': recall, 'false_per_scene': false_per_scene}


def score_mask(cases: list[dict], mask_fn) -> dict:
    leaks, kept = [], []
    for index, case in enumerate(cases):
        result = mask_fn(case['text'])
        masked = result[0] if isinstance(result, tuple) else result
        for item in case.get('must_mask', []):
            if item['span'] in masked:
                leaks.append({'index': index, 'kind': item.get('kind')})
        for value in case.get('must_keep', []):
            if value not in masked:
                kept.append({'index': index, 'value': value})
    return {'leaks': leaks, 'kept': kept}
