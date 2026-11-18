"""目的と予定の好みを、推測から本人確認へつなぐ。"""
from __future__ import annotations
from datetime import date, datetime
from zoneinfo import ZoneInfo
import json
import secrets

JST=ZoneInfo("Asia/Tokyo")
# Keep wording in the prompt files; these maps only route answer numbers.
QUESTIONS_GOAL={1:"大切にしたいこと",2:"時間の使い方",3:"関係",4:"仕事と学び",5:"健康と休息",6:"今年の目標",7:"長期の目標",8:"減らしたい負担",9:"優先順位",10:"家庭",11:"支援の範囲",12:"進捗の実感",13:"休止すること"}
QUESTIONS_TWIN={1:"集中しやすい時間",2:"一日の予定数",3:"移動の余裕",4:"休憩の間隔",5:"食事の時間",6:"集中時間",7:"知らせの頻度",8:"候補の数",9:"変更の知らせ方",10:"確認の形",11:"共有する情報",12:"条件が競合した時の選び方"}


def _at(now):
    if isinstance(now,str):
        value=datetime.fromisoformat(now.replace("Z","+00:00"))
        return value.replace(tzinfo=JST) if value.tzinfo is None else value.astimezone(JST)
    if isinstance(now,datetime):return now.replace(tzinfo=JST) if now.tzinfo is None else now.astimezone(JST)
    raise ValueError("now は日時で指定してください")


def import_answers(ledger, answers, kind, now):
    if kind not in {"goal","twin"}:raise ValueError("kind は goal または twin です")
    if not isinstance(answers,list):raise ValueError("answers はリストで指定してください")
    at=_at(now).isoformat(); mapping=QUESTIONS_GOAL if kind=="goal" else QUESTIONS_TWIN
    imported=[]
    with ledger.tx():
        for answer in answers:
            if not isinstance(answer,dict):raise ValueError("回答は辞書で指定してください")
            try:q_no=int(answer["q_no"])
            except (KeyError,TypeError,ValueError) as exc:raise ValueError("q_no が必要です") from exc
            if q_no not in mapping:raise ValueError(f"{kind} のq_noが範囲外です: {q_no}")
            value=answer.get("answer")
            if not isinstance(value,str) or not value.strip():continue
            value=value.strip(); destination=mapping[q_no]
            if kind=="goal":
                existing=ledger.conn.execute("SELECT node_id FROM goal_nodes WHERE parent_id IS NULL AND kind=? AND text=? ORDER BY updated_at DESC LIMIT 1",(destination,value)).fetchone()
                if existing: imported.append(existing["node_id"]);continue
                node_id=secrets.token_hex(12)
                ledger.conn.execute("INSERT INTO goal_nodes(node_id,parent_id,kind,text,state,horizon,updated_at) VALUES(?,?,?,?,?,?,?)",(node_id,None,destination,value,"推測",None,at))
                ledger._record_history("goal:"+node_id,{"goal_node":{"before":None,"after":{"node_id":node_id,"kind":destination,"text":value,"state":"推測"}}},"目的の回答を推測として記録",at,actor="ボット")
                imported.append(node_id)
            else:
                pref_id=ledger.set_pref(destination,value,"推測",now=at)
                imported.append(pref_id)
    return imported


def confirm(ledger, ids, now):
    at=_at(now).isoformat(); confirmed=[]
    with ledger.tx():
        for identifier in ids:
            row=ledger.conn.execute("SELECT * FROM goal_nodes WHERE node_id=?",(identifier,)).fetchone()
            if row:
                before=row["state"]
                if before!="確定":
                    ledger.conn.execute("UPDATE goal_nodes SET state='確定',updated_at=? WHERE node_id=?",(at,identifier))
                    ledger._record_history("goal:"+identifier,{"state":{"before":before,"after":"確定"}},"本人が目的を確認",at,actor="本人")
                confirmed.append(identifier);continue
            row=ledger.conn.execute("SELECT * FROM twin_prefs WHERE pref_id=?",(identifier,)).fetchone()
            if row:
                before=row["state"]
                if before!="確定":
                    ledger.conn.execute("UPDATE twin_prefs SET state='確定' WHERE pref_id=?",(identifier,))
                    ledger._record_history("pref:"+identifier,{"state":{"before":before,"after":"確定"}},"本人が好みを確認",at,actor="本人")
                confirmed.append(identifier);continue
            raise KeyError(identifier)
    return confirmed


def gaps(ledger, cases, now):
    current=_at(now); cutoff=current.timestamp()-14*24*60*60
    nodes={row["node_id"]:dict(row) for row in ledger.conn.execute("SELECT node_id,parent_id,kind,text,state,updated_at FROM goal_nodes")}
    def root_for(node_id):
        root=node_id; parent=nodes[node_id].get("parent_id"); visited=set()
        while parent in nodes and parent not in visited:
            visited.add(parent); root=parent; parent=nodes[parent].get("parent_id")
        return root
    latest={node_id:(_at(node["updated_at"]) if node.get("updated_at") else None) for node_id,node in nodes.items()}
    for row in ledger.conn.execute("SELECT node_id,at FROM goal_progress"):
        if row["node_id"] in nodes:
            stamp=_at(row["at"])
            if latest[row["node_id"]] is None or stamp>latest[row["node_id"]]:latest[row["node_id"]]=stamp
    for row in ledger.conn.execute("SELECT node_id,at FROM success_fit"):
        if row["node_id"] in nodes:
            stamp=_at(row["at"])
            if latest[row["node_id"]] is None or stamp>latest[row["node_id"]]:latest[row["node_id"]]=stamp
    for case in cases or []:
        if not isinstance(case,dict):continue
        refs=[]
        for key in ("goal_node_id","node_id"):
            if case.get(key):refs.append(case[key])
        refs.extend(case.get("goal_node_ids",[]) if isinstance(case.get("goal_node_ids"),list) else [])
        updated=case.get("updated_at")
        if not updated:continue
        try:stamp=_at(updated)
        except (ValueError,TypeError):continue
        for node_id in refs:
            if node_id in latest and (latest[node_id] is None or stamp>latest[node_id]):latest[node_id]=stamp
    branch_activity={}
    for node_id,stamp in latest.items():
        root=root_for(node_id)
        if stamp and (root not in branch_activity or stamp>branch_activity[root]):branch_activity[root]=stamp
    result=[]
    for node_id,node in nodes.items():
        if node.get("parent_id") is not None or node["state"] in {"休み中","達成","やめた"}:continue
        last=branch_activity.get(node_id)
        if last is None or last.timestamp()<=cutoff:
            result.append({"node_id":node_id,"kind":node["kind"],"text":node["text"],"last_activity":last.isoformat() if last else None,"days_since_activity":None if last is None else (current-last).total_seconds()/86400})
    return sorted(result,key=lambda item:(item["last_activity"] or "",item["node_id"]))
