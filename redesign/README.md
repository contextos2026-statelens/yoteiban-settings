# redesign/ 予定ばん 再設計版（試作）

本番には入っていない。設計は DESIGN.md。

- lib/yoteiban_rd/：箱で使う計算係・急ぎの判定・決まりの切り出し・毎晩の見直し・委任送信・並べて試す。
- mac/：Mac mini の見張り番（まだ置かない）。導入は mac/INSTALL.md。
- skills/：再設計版のスキル（土台・届いたもの・知らせ・毎晩・書く・委任）。本番のスキルは変えない。
- config/：設定の例（本物の値は入れない）。
- tests/：`cd redesign && python3 -m unittest discover -s tests`。tools/stage2/delegation.py とリポジトリの seihon.md を使う（無ければ箱の写し）。
