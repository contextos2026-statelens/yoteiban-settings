# 新しい持ち主の確認表

個人の値（名前・メール・電話・カレンダーID・鍵・Webhook・自分の道）は、このリポジトリに書かない。下はやることの確認だけ。

1. 公開リポジトリ `yoteiban-settings` を取る。
2. `redesign/config/settings.example.json` と `core/config.example.json` を、自分の箱（または実行場所）へ写す。例のまま本番に使わない。
3. 写した設定の `tools` か、環境変数で道具の置き場を定める。
   - `YOTEIBAN_SETTINGS` / `YRD_SETTINGS`（設定ファイルの場所）
   - `YOTEIBAN_SEIHON` / `YRD_SEIHON`（正本の写し）
   - `YOTEIBAN_REDESIGN_LIB` / `YRD_REDESIGN_LIB`（計算係）
   - `YOTEIBAN_STATE` / `YRD_STATE`（状態の置き場）
   - `YOTEIBAN_TOOLS` / `YRD_TOOLS`（queue.py などの道具）
   - `YOTEIBAN_QUEUE` / `YRD_QUEUE`（処理状態ファイル）
4. 鍵と Webhook の値は Git に入れない。権限を絞ったファイルか環境変数に置く。
5. 自分のカレンダー・Notion・入口（メールやチャット）・Mac の常駐は、本人が自分のアカウントでつなぐ。他の持ち主の台帳・カレンダー・Notion を使わない。
6. 初回設定（`skills/setup/SKILL.md`）を、質問カードで1つずつ進める。家族の名簿は空から始める。用意するものは `docs/prepare_items.md`（要るものを先に伝え、ボットが作れるものは作り、本人の一歩だけ頼む。鍵は入力欄だけ。支払い・有料登録はしない）。
7. 短い架空データの自己試験を、実データには書かずに行う。
8. 本番のスキルやルーチンへの適用は、確認表と本人の「はい」のあとだけ。勝手に上書きしない。
9. 決まりの版が上がったあとは、足りない質問だけを追加設定（同じスキル）で聞く。すでに答えのある項目や、つながっている連携は聞かない。
10. 配る範囲には、予定ばん以外のスキルを含めてよい。個人の値は、どのスキルにも置かない。
