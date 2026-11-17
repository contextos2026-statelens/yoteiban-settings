---
name: yoteiban-intake-lite
description: 予定ばん（再設計版）が、見張り番に起こされたとき、または定期の見回りで、届いた1件を振り分けるときに使う。yoteiban-core-lite と一緒に読む。
---

# 届いた1件の振り分け（再設計版）

読む章：`rules_slice --task intake`（2・3・5・6・7・8・9・12・13・19・20・25章）。19・20章は急ぎの段階と知らせ方のために入れてある。

1. 起こされたときは、渡された依頼IDの1件だけを、入口の参照（番号・メールID）から読む。Webhook には本文が入っていない。読めなければ「読めていない入口」にして朝のまとめに1行出す。
2. 台帳に記録する：`casebook.py record`（依頼IDで冪等。同じ依頼は二重に作らない）。処理の成否は台帳が正。「起こされた」は「処理が終わった」ではない。
3. 誰宛てか・1対1か・ミュートか・予定に関係あるかを章のとおりに決める。見張り番の仕分け（外部AIの案）は参考で、決めるのは予定ばん。
4. 予定に関係あるものは案件にする（`casebook.py add-case`）。日時が欠けていれば infer の章を出して読む。
5. 急ぎかどうかは計算係の `urgency` で決める（下の入力の形）。段階の意味と知らせ方は 19・20章と notify スキル。急ぎでなければ朝のまとめの候補に置き、何も知らせない。
6. 終わったら処理を「完了」にする。途中で止まったら「失敗・要再試行」にし、次の見回りで同じ依頼IDから続ける。

## 計算係 `urgency` の入力（JSON を標準入力で渡す）

予定なし（記録だけ。第8・9章で項目を作らないもの）は `urgency` にかけない。急ぎにしないで記録だけにする。

```
cd <redesign/lib> && echo '<JSON>' | python3 -m yoteiban_rd.cli urgency
```

いちばん小さい形（時刻はどれも時間帯つき。「JST」か「+09:00」）。動く起点の時刻（待ち合わせ・開始・期限）を `time` に入れ、書かれていない所要は入れない：

```json
{"item": {"title": "通院の時刻が早まった（駅で待ち合わせ）", "time": "2026-11-19 08:20 JST", "travel_min": 30},
 "context": {"now": "2026-11-18 21:30 JST", "next_summary": "2026-11-19 07:00 JST"}}
```

この答えは `tier` 今すぐ・`deliver` now・`action_deadline` 7:50・`slack_min` 50・`estimated` true（準備の時間が無いので0分で計算）。

- 必ず要る：`item.title`、`context.now`、`context.next_summary`（次の朝のまとめの時刻）。
- `item.time` は開始か期限。無ければ「ふつう（保留）」。
- 移動・準備が書かれていなければ入れない。0分で計算して `estimated` と `assumed` に出す（締切を勝手に早めない）。知らせるときは「準備の時間を入れると、もっと早くなります」と添える。
- 第19章の点を付けたら `item.importance`（緊急・高・中・低）か `item.importance_score`、「待てない」なら `item.cannot_wait: true` を渡す。時刻の段階と比べて高い方が採られる。
- 通知の型・夜の設定・静かな時間・次の配信時刻は記憶の値を `context` に渡す（項目は notify スキルの表）。
- 返り値：`tier`（今すぐ／今日中／朝一番／ふつう／急ぎにしない／過ぎていた）、`deliver`（now／next_delivery／morning_top／morning／log）、`reasons`、`action_deadline`、`slack_min`、`estimated`、`assumed`、`level`、`remind_at`。段階と知らせ方は必ず対応する（今すぐ→now、今日中→now か next_delivery、朝一番→morning_top）。
- 入力が足りない・知らない項目があると、終了コード2で `{"error": "...", "example": {...}}` を返す。そのときは推測で埋めず、足りない項目を直して出し直す。
