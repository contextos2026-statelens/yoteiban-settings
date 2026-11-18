"""確定した通知形式と明示的な日時表現を安全側で読み取る。"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import re
from yoteiban_rd.calc import check_weekdays

JST = ZoneInfo("Asia/Tokyo")
_WEEK = {"月":0,"火":1,"水":2,"木":3,"金":4,"土":5,"日":6}


def _base_date(value) -> date:
    if isinstance(value, datetime): return value.astimezone(JST).date() if value.tzinfo else value.date()
    if isinstance(value, date): return value
    if isinstance(value, str): return date.fromisoformat(value[:10])
    raise ValueError("base_date は日付またはISO日付文字列で指定してください")


def _find_date(text: str, base: date) -> tuple[date | None, str | None]:
    # Explicit year first, then month/day forms (weekday is checked below).
    patterns = [
        (r"(?P<y>20\d{2})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日?", True),
        (r"(?P<m>\d{1,2})月(?P<d>\d{1,2})日?(?:\s*[(（][月火水木金土日][)）])?", False),
        (r"(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:\s*[(（][月火水木金土日][)）])?", False),
    ]
    for pattern, has_year in patterns:
        m = re.search(pattern, text)
        if m:
            year = int(m.group("y")) if has_year else base.year
            month, day = int(m.group("m")), int(m.group("d"))
            try: result = date(year, month, day)
            except ValueError: return None, m.group(0)
            if not has_year and result < base - timedelta(days=31):
                try: result = date(year + 1, month, day)
                except ValueError: return None, m.group(0)
            return result, m.group(0)
    m = re.search(r"来週(?:の)?([月火水木金土日])(?:曜日|曜)?", text)
    if m:
        monday = base - timedelta(days=base.weekday()) + timedelta(days=7)
        return monday + timedelta(days=_WEEK[m.group(1)]), m.group(0)
    offsets = (("明後日",2),("あさって",2),("明日",1),("きょう",0),("今日",0))
    for word, delta in offsets:
        if word in text: return base + timedelta(days=delta), word
    m = re.search(r"(?<!\d)(\d{1,2})日", text)
    if m:
        day = int(m.group(1)); year, month = base.year, base.month
        try: result = date(year,month,day)
        except ValueError: return None,m.group(0)
        if result < base:
            month += 1
            if month == 13: year,month = year+1,1
            try: result = date(year,month,day)
            except ValueError: return None,m.group(0)
        return result,m.group(0)
    return None,None


def _find_time(text: str) -> tuple[time | None, str | None]:
    m = re.search(r"(?P<ampm>午前|午後)?\s*(?P<h>\d{1,2})(?:[:：](?P<min>\d{2}))\s*(?:時)?", text)
    if m:
        h, minute = int(m.group("h")), int(m.group("min"))
        if m.group("ampm") == "午後" and h < 12: h += 12
        if m.group("ampm") == "午前" and h == 12: h = 0
        if h < 24 and minute < 60: return time(h,minute),m.group(0).strip()
    m = re.search(r"(?P<ampm>午前|午後)?\s*(?P<h>\d{1,2})時(?P<tail>半|\d{1,2}分)?", text)
    if m:
        h=int(m.group("h")); tail=m.group("tail")
        minute=30 if tail=="半" else int(tail[:-1]) if tail else 0
        if m.group("ampm")=="午後" and h<12:h+=12
        if m.group("ampm")=="午前" and h==12:h=0
        if h<24 and minute<60:return time(h,minute),m.group(0).strip()
    if "午後" in text:return None,"午後"
    return None,None


def _kind(text: str) -> str | None:
    if re.search(r"取消|取り消|中止|キャンセル",text): return "取消"
    if re.search(r"変更|延期|時間をずら|場所を変",text): return "変更"
    if re.search(r"締切|期限|までに|提出",text): return "締切"
    if re.search(r"お願い|依頼|用意して|持ってきて",text): return "依頼"
    if re.search(r"誘い|遊びませんか|来ませんか|一緒に|会えますか",text): return "誘い"
    if re.search(r"お知らせ|ご案内|連絡です|学校.{0,12}(?:通知|お知らせ|連絡)",text): return "お知らせ"
    if re.search(r"予定|会う|集合|開催|予約|受け付けました|招待|集まり|会議|面談|食事|通院|歯科|授業|説明会|読書会",text): return "予定"
    return None


def _title(text: str, date_text: str | None, time_text: str | None) -> str:
    value=text
    for part in (date_text,time_text):
        if part:value=value.replace(part," ")
    value=re.sub(r"(?:明後日|あさって|明日|今日|きょう|来週の?|\d{1,2}日|\d{1,2}月|\d{1,2}/\d{1,2})"," ",value)
    value=re.sub(r"[\s、。,.!?！？:：]+"," ",value).strip()
    value=re.sub(r"(?:の)?予定(?:です|があります)?$|(?:の)?締切(?:です)?$|(?:です)$","",value)
    value=re.sub(r"^(?:予定|日時|日程|お知らせ|ご案内|の|に|は|が)\s*","",value)
    return (value or "予定")[:80]


def _read_sentence(text: str, base: date) -> dict | None:
    kind=_kind(text)
    day,date_text=_find_date(text,base)
    if not kind or day is None:return None
    tod,time_text=_find_time(text)
    mismatches=check_weekdays(text,base)
    ambiguous=bool(re.search(r"かも|かもしれ|未定|調整中|行けたら|予定ですか",text))
    definite=bool(re.search(r"予約(?:が)?(?:確定|完了|を承りました)|確定しました|参加します|招待(?:状)?|決まりました|学校.{0,12}(?:通知|お知らせ|連絡)|予定です|予定があります|集合します|開催します",text))
    certainty="推測" if mismatches else "未定" if ambiguous else "確定" if definite else "推測"
    start=datetime.combine(day,tod,tzinfo=JST).isoformat(timespec="minutes") if tod else day.isoformat()
    return {"kind":kind,"title":_title(text,date_text,time_text),"event_start":start,"date_only":tod is None,
            "certainty":certainty,"needs_reply":bool(re.search(r"返事|返信|来られますか|どうですか|参加でき",text)) or kind=="誘い",
            "reply_by":None,"questions":[],"date_text":date_text,"time_text":time_text}


def read(text: str, base_date) -> list[dict] | None:
    """確かな定型情報のみ返す。不確実な文はAIに渡すため None。"""
    if not isinstance(text,str) or not text.strip(): return None
    base=_base_date(base_date)
    # Split only at sentence boundaries; each segment may contain one event.
    sentences=[s.strip() for s in re.split(r"[。！？!?\n]+",text) if s.strip()]
    items=[]
    definite_context=bool(re.search(r"予約(?:が)?(?:確定|完了|を承りました)|確定しました|参加します|招待(?:状)?|決まりました|学校.{0,12}(?:通知|お知らせ|連絡)",text))
    for sentence in sentences:
        item=_read_sentence(sentence,base)
        if item:
            if definite_context:item["certainty"]="確定"
            items.append(item)
    if not items:
        whole = _read_sentence(text.strip(),base)
        if whole: items.append(whole)
    if not items:return None
    # An explicit weekday contradiction is still useful as a low-confidence item;
    # other unresolved wording is sent to the AI reader instead.
    if not check_weekdays(text,base) and any(re.search(r"かも|かもしれ|未定|調整中|行けたら",s) for s in sentences): return None
    return items
