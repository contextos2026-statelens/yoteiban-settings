"""外部AIへ渡す文字列の伏せ字と残存検査。"""
from __future__ import annotations
import re
from typing import Any
from yoteiban_watch.mask import mask as _base_mask

class MaskLeak(ValueError):
    """伏せ字の後に秘匿値が残っている。"""

# The base mask owns the actual replacement rules. These expressions are only
# used to retain local evidence for the post-mask leak check.
_SENSITIVE = [
    re.compile(r"[A-Za-z0-9._%+-]+(?:[ \t]*[\r\n][ \t]*)?@(?:[ \t]*[\r\n][ \t]*)?[A-Za-z0-9.-]+(?:[ \t]*[\r\n][ \t]*)?\.[A-Za-z]{2,}"),
    re.compile(r"https?://\S+"),
    re.compile(r"\+?\d[\d\-－−ー 　()（）]{7,}\d"),
    re.compile(r"〒?\s?\d{3}[\-－ー]\d{4}"),
    re.compile(r"\d{6,}"),
    re.compile(r"[一-龥ぁ-んァ-ヶ]{1,8}(?:先生|さん|さま|様|くん|君|ちゃん|氏|殿|先輩|コーチ|監督|部長|課長|社長)"),
    re.compile(r"(?:東京都|北海道|京都府|大阪府|[一-龥]{2,3}県|[一-龥]{1,5}市|[一-龥]{1,5}区)[^\s、。]{0,30}"),
]


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def mask(text: str, known_names: list[str] | None = None) -> tuple[str, dict[str, str]]:
    """伏せ字本文と、残存検査用のローカル対応表を返す。"""
    source = str(text or "")
    values = set(name for name in (known_names or []) if isinstance(name, str) and name)
    for pattern in _SENSITIVE:
        values.update(m.group(0) for m in pattern.finditer(source))
    # Keep names out of the user-facing placeholder by mapping them to a unique
    # marker through the existing masker, then replace that marker afterwards.
    name_map = {name: f"YOTEIBAN_MASK_{i:04d}" for i, name in enumerate(sorted(values, key=len, reverse=True))}
    masked = _base_mask(source, name_map)
    mapping: dict[str, str] = {}
    for name, marker in name_map.items():
        placeholder = "（情報）"
        if marker in masked:
            masked = masked.replace(f"（{marker}）", placeholder).replace(marker, placeholder)
        mapping[placeholder + f"#{len(mapping)}"] = name
    # The returned text uses a generic placeholder; map values are sufficient for
    # exact and partial residual checks, and the keys are non-secret labels.
    return masked, mapping


def leaks(masked: str, mapping: dict[str, str]) -> list[str]:
    """Return labels for any original value wholly or substantially remaining."""
    found = []
    for label, original in mapping.items():
        if not original:
            continue
        normalized = "".join(original.split())
        candidate = "".join(str(masked).split())
        # Test exact value and contiguous chunks of at least three characters
        # (or half the value for very short identifiers).
        if normalized and normalized in candidate:
            found.append(label)
            continue
        width = max(2, (len(normalized) + 1) // 2)
        if any(normalized[i:i + width] in candidate for i in range(max(0, len(normalized) - width + 1))):
            found.append(label)
    return found


def guard(text: str, known_names: list[str] | None = None) -> str:
    masked, mapping = mask(text, known_names)
    remaining = leaks(masked, mapping)
    if remaining:
        raise MaskLeak("伏せ字の消し残しがあります")
    return masked
