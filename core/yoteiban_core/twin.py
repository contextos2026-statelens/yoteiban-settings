"""非公開の好み推定と答え合わせ。外部AIや承認には接続しない。"""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json

JST=ZoneInfo("Asia/Tokyo")


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def _decode(value):
    try:return json.loads(value)
    except (TypeError,json.JSONDecodeError):return value

def _canon(value):return _json(value)

def _prefs(prefs):
    out=[]
    if isinstance(prefs,dict):
        iterable=[]
        for topic,value in prefs.items():
            if isinstance(value,dict):iterable.append({"topic":topic,**value})
            else:iterable.append({"topic":topic,"value":value,"state":"推測"})
    elif isinstance(prefs,list):iterable=prefs
    else:iterable=[]
    for item in iterable:
        if not isinstance(item,dict) or not item.get("topic"):continue
        out.append({"topic":str(item["topic"]),"value":item.get("value"),"state":item.get("state","推測")})
    return out

def _weight(state):return {"確定":1.0,"推測":0.5,"古い":0.0}.get(state,0.5)

def _option_text(option):
    if isinstance(option,dict):return " ".join(str(v) for v in option.values())
    return str(option)

def _normalize(value):return "".join(str(value).strip().casefold().split())

def _matches(option,pref):
    topic=pref["topic"]; expected=pref.get("value")
    if expected is None:return False
    actual=None
    if isinstance(option,dict):
        actual=option.get(topic)
        attrs=option.get("attributes")
        if actual is None and isinstance(attrs,dict):actual=attrs.get(topic)
    if actual is not None:return _normalize(actual)==_normalize(expected)
    expected_text=_normalize(expected)
    return len(expected_text)>=2 and expected_text in _normalize(_option_text(option))


def rank(options, prefs):
    """設定値と選択肢の一致で並べる。推測の好みは確定の半分の重み。"""
    preferences=[p for p in _prefs(prefs) if _weight(p["state"])>0]
    ranked=[]
    for index,option in enumerate(options or []):
        matched=[]; score=0.0
        for pref in preferences:
            if _matches(option,pref):
                score+=_weight(pref["state"]); matched.append(pref)
        if matched:
            reasons=[f"{p['topic']}の好み（{p['state']}）に合う" for p in matched]
            why="、".join(reasons)
        else:why="登録された好みとの一致は見つかりません"
        ranked.append({"option":option,"score":score,"why":why,"_index":index})
    ranked.sort(key=lambda item:(-item["score"],item["_index"]))
    for item in ranked:item.pop("_index",None)
    return ranked


def predict(topic, prefs):
    candidates=[p for p in _prefs(prefs) if p["topic"]==topic and p.get("value") is not None]
    if not candidates:return {"topic":topic,"predicted":None,"state":"不明","why":"この話題の好みがありません"}
    chosen=max(candidates,key=lambda p:_weight(p["state"]))
    return {"topic":topic,"predicted":chosen["value"],"state":chosen["state"],"why":f"{chosen['state']}の好みをもとにした推測です"}


def observe(ledger, topic, actual, now):
    at=now if isinstance(now,str) else now.isoformat()
    parsed=datetime.fromisoformat(at.replace("Z","+00:00"))
    at=(parsed.replace(tzinfo=JST) if parsed.tzinfo is None else parsed.astimezone(JST)).isoformat()
    with ledger.tx():
        observation_id=hashlib.sha256((topic+"|"+at+"|"+_json(actual)).encode("utf-8")).hexdigest()[:24]
        ledger.conn.execute("INSERT OR REPLACE INTO twin_observations(id,at,topic,observed) VALUES(?,?,?,?)",(observation_id,at,topic,_json(actual)))
        row=ledger.conn.execute("SELECT id,predicted FROM twin_predictions WHERE topic=? AND actual IS NULL ORDER BY at DESC,id DESC LIMIT 1",(topic,)).fetchone()
        if row is None:return {"topic":topic,"predicted":None,"actual":actual,"hit":None}
        predicted=_decode(row["predicted"])
        hit=int(_canon(predicted)==_canon(actual))
        ledger.conn.execute("UPDATE twin_predictions SET actual=?,hit=? WHERE id=?",(_json(actual),hit,row["id"]))
        return {"topic":topic,"predicted":predicted,"actual":actual,"hit":hit}
