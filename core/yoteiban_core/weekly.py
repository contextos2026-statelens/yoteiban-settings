"""週ごとの技術・適合・生活フィードバックを集計する。"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo
import json
import math

JST = ZoneInfo("Asia/Tokyo")
FEEDBACK_TAGS = ("役立った", "うるさい", "足りない")


def _dt(value, end_of_day=False):
    if value is None: return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=JST) if value.tzinfo is None else value.astimezone(JST)
    if isinstance(value, date):
        return datetime.combine(value, time.max if end_of_day else time.min, tzinfo=JST)
    text=str(value)
    try: result=datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            d=date.fromisoformat(text[:10])
            return datetime.combine(d,time.max if end_of_day else time.min,tzinfo=JST)
        except ValueError: return None
    return result.replace(tzinfo=JST) if result.tzinfo is None else result.astimezone(JST)


def _week_range(week_start, now):
    start = _dt(week_start)
    if start is None: raise ValueError("week_start は有効な日付で指定してください")
    start = datetime.combine(start.date(),time.min,tzinfo=JST)
    end = start + timedelta(days=7)
    current = _dt(now)
    if current is None: raise ValueError("now は有効な日時で指定してください")
    return start,end,current


def _in_week(value, start, end):
    d=_dt(value)
    return d is not None and start <= d < end


def _median_p90(values):
    if not values:return {"median_seconds":None,"p90_seconds":None,"count":0}
    ordered=sorted(values)
    index=max(0,math.ceil(0.9*len(ordered))-1)
    return {"median_seconds":median(ordered),"p90_seconds":ordered[index],"count":len(ordered)}


def _status_changes(text):
    try: value=json.loads(text or "{}")
    except (TypeError,json.JSONDecodeError): return []
    out=[]
    def visit(v):
        if isinstance(v,dict):
            if "before" in v and "after" in v: out.append((v.get("before"),v.get("after")))
            else:
                for child in v.values():visit(child)
        elif isinstance(v,list):
            for child in v:visit(child)
    visit(value)
    return out


def _reply_delays(ledger, start, end, current):
    rows=ledger.conn.execute("SELECT case_id,kind,title,summary,status,due_at FROM cases WHERE due_at IS NOT NULL").fetchall()
    delays=[]
    waiting={"未確定","確認中","返事なし","承認待ち"}
    for row in rows:
        deadline=_dt(row["due_at"])
        if deadline is None or deadline >= current:continue
        events=ledger.conn.execute("SELECT at,change,reason FROM history WHERE case_id=? ORDER BY at,h_id",(row["case_id"],)).fetchall()
        saw_no_reply=row["status"]=="返事なし"
        responded=None
        for event in events:
            at=_dt(event["at"]); found=_status_changes(event["change"])
            if any("返事なし" in (before,after) for before,after in found):saw_no_reply=True
            if at and at>=deadline and ("返事" in (event["reason"] or "") or "返信" in (event["reason"] or "") or "回答" in (event["reason"] or "")):
                responded=at;break
            if at and at>=deadline and any(before in waiting and after not in waiting for before,after in found):
                responded=at;break
        markers=" ".join((row["kind"] or "",row["title"] or "",row["summary"] or ""))
        is_reply=saw_no_reply or row["kind"]=="誘い" or any(w in markers for w in ("返事","返信","回答"))
        if not is_reply:continue
        if not _in_week(deadline,start,end) and not (responded and _in_week(responded,start,end)):continue
        endpoint=responded or current
        delays.append(max(0.0,(endpoint-deadline).total_seconds()/86400))
    return {"count":len(delays),"average_days_late":sum(delays)/len(delays) if delays else 0.0}


def _late_reminders(ledger, start, end):
    rows=ledger.conn.execute("SELECT o.result,c.event_start FROM operations o JOIN cases c ON c.case_id=o.case_id WHERE o.kind='reminder' AND o.state='成功（読み戻し済み）'").fetchall()
    count=0
    for row in rows:
        try: result=json.loads(row["result"] or "{}")
        except (TypeError,json.JSONDecodeError): result={}
        completed=None
        for key in ("completed_at","completedAt","completed_time","completed"):
            if isinstance(result.get(key),str):
                completed=_dt(result[key])
                if completed:break
        planned=_dt(row["event_start"])
        if completed and planned and completed>planned and _in_week(completed,start,end):count+=1
    return count


def week_report(ledger, week_start, now) -> dict:
    start,end,current=_week_range(week_start,now)
    feedback={tag:{"count":0,"text":[]} for tag in FEEDBACK_TAGS}
    for row in ledger.conn.execute("SELECT at,tag,text FROM success_life ORDER BY at,tag"):
        if _in_week(row["at"],start,end) and row["tag"] in feedback:
            feedback[row["tag"]]["count"]+=1; feedback[row["tag"]]["text"].append(row["text"])
    missed=sum(1 for row in ledger.conn.execute("SELECT at,reason FROM history") if _in_week(row["at"],start,end) and row["reason"]=="見落とし")
    user_edits=sum(1 for row in ledger.conn.execute("SELECT at,actor FROM history") if _in_week(row["at"],start,end) and row["actor"]=="本人")
    delays=_reply_delays(ledger,start,end,current)
    lateness=_late_reminders(ledger,start,end)
    tech_rows=[dict(r) for r in ledger.conn.execute("SELECT started_at,ended_at,stage,cost_yen FROM latency") if _in_week(r["started_at"],start,end)]
    durations=[]; cost=0.0
    for row in tech_rows:
        cost+=float(row["cost_yen"] or 0)
        a,b=_dt(row["started_at"]),_dt(row["ended_at"])
        if a and b and b>=a:durations.append((b-a).total_seconds())
    metrics={}
    for row in ledger.conn.execute("SELECT at,metric,value FROM success_tech"):
        if _in_week(row["at"],start,end):metrics.setdefault(row["metric"],[]).append(float(row["value"]))
    accuracy_values=[v for k,vs in metrics.items() if k in {"read_accuracy","reading_accuracy","読み取り正確さ"} for v in vs]
    duplicate_values=[v for k,vs in metrics.items() if k in {"duplicate","duplicates","duplicate_ops","double","double_operation","二重操作"} for v in vs]
    technical={"reading_accuracy":sum(accuracy_values)/len(accuracy_values) if accuracy_values else None,
               "duplicate_count":sum(duplicate_values),"latency":_median_p90(durations),"cost_yen":cost,
               "latency_samples":len(tech_rows)}
    nodes={r["node_id"]:dict(r) for r in ledger.conn.execute("SELECT node_id,parent_id,kind,text,state FROM goal_nodes")}
    def branch_root(node_id):
        node=nodes[node_id]; root=node_id; parent=node.get("parent_id"); visited=set()
        while parent and parent in nodes and parent not in visited:
            visited.add(parent); root=parent; parent=nodes[parent].get("parent_id")
        return root
    progress={}
    for node_id,node in nodes.items():
        root=branch_root(node_id)
        if root not in progress:
            root_node=nodes[root]
            progress[root]={"node_id":root,"text":root_node["text"],"kind":root_node["kind"],"state":root_node["state"],"count":0,"notes":[]}
    for row in ledger.conn.execute("SELECT node_id,at,note FROM goal_progress ORDER BY at"):
        if not _in_week(row["at"],start,end) or row["node_id"] not in nodes:continue
        root=branch_root(row["node_id"]); entry=progress[root]
        entry["count"]+=1
        if row["note"]:entry["notes"].append(row["note"])
    adoption_events={"adopted":0,"rejected":0}
    for row in ledger.conn.execute("SELECT at,change FROM history"):
        if not _in_week(row["at"],start,end):continue
        try: change=json.loads(row["change"] or "{}")
        except (TypeError,json.JSONDecodeError):continue
        plan=change.get("plan_status")
        if isinstance(plan,dict):
            status=plan.get("after")
            if status=="採用":adoption_events["adopted"]+=1
            elif status=="却下":adoption_events["rejected"]+=1
    decisions=adoption_events["adopted"]+adoption_events["rejected"]
    purpose={"goal_progress":list(progress.values()),"proposal_adoption":{"adopted":adoption_events["adopted"],"rejected":adoption_events["rejected"],"rate":adoption_events["adopted"]/decisions if decisions else None}}
    return {"week_start":start.date().isoformat(),"week_end":(end.date()-timedelta(days=1)).isoformat(),"feedback":feedback,
            "missed_count":missed,"reply_delay":delays,"user_edit_count":user_edits,"late_reminder_clues":lateness,
            "technical":technical,"purpose":purpose}


def to_text(report: dict) -> str:
    feedback=report.get("feedback",{}); parts=[]
    for tag in FEEDBACK_TAGS:
        value=feedback.get(tag,{}); texts=value.get("text",[])
        note=("「"+str(texts[0]).replace("\n"," ")[:32]+("…" if len(str(texts[0]))>32 else "")+"」") if texts and texts[0] else ""
        parts.append(f"{tag}{value.get('count',0)}件"+(f" {note}" if note else ""))
    line1="感想: "+" / ".join(parts)
    delay=report.get("reply_delay",{})
    line2=f"見落とし: {report.get('missed_count',0)}件。返事の遅れ: {delay.get('count',0)}件、平均{delay.get('average_days_late',0):.1f}日。"
    line3=f"本人の直し: {report.get('user_edit_count',0)}件。遅刻の手がかり: {report.get('late_reminder_clues',0)}件。"
    tech=report.get("technical",{}); lat=tech.get("latency",{}); acc=tech.get("reading_accuracy")
    acc_text="未計測" if acc is None else f"{acc:.0%}"
    med=lat.get("median_seconds"); p90=lat.get("p90_seconds"); latency_text="未計測" if med is None else f"中央値{med:.1f}秒・90%値{p90:.1f}秒"
    line4=f"技術: 読み取り{acc_text}、二重{tech.get('duplicate_count',0)}件、待ち時間{latency_text}、費用{tech.get('cost_yen',0):.0f}円。"
    purpose=report.get("purpose",{}); adopt=purpose.get("proposal_adoption",{}); rate=adopt.get("rate")
    rate_text="未計測" if rate is None else f"{rate:.0%}"
    line5=f"目的: 進みのある枝{len(purpose.get('goal_progress',[]))}本、提案採用率{rate_text}。"
    return "\n".join((line1,line2,line3,line4,line5))


def record_feedback(ledger, text, tag, now):
    if tag not in FEEDBACK_TAGS:raise ValueError("tag は役立った・うるさい・足りないのいずれかです")
    at=_dt(now)
    if at is None:raise ValueError("now は有効な日時で指定してください")
    with ledger.tx():ledger.conn.execute("INSERT OR IGNORE INTO success_life(at,tag,text) VALUES(?,?,?)",(at.isoformat(),tag,str(text)))
    return True
