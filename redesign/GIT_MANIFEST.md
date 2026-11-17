# Git に入れるファイル一覧（再設計版）

置き場所：リポジトリ contextos2026-statelens/yoteiban-settings の **redesign/** の下に、この表の左の配置のまま置く。既存のファイル（tools/・skills/・seihon.md など）は変えない。

受け渡し場所：ボット固有ストアの redesign-handoff/redesign/。git hash-object の値で照合する。

ファイル数：34（この表）＋ GIT_MANIFEST.md ＝ 35

| リポジトリ内の場所 | git hash-object |
|---|---|
| redesign/DESIGN.md | 495641d7507e9c15938ba9c45ca3c81222c70964 |
| redesign/README.md | d5df2996471cf1187fa92738bfd2f9a1f7f94bc2 |
| redesign/config/settings.example.json | 892d9b0dc97ab4881d9655427a846f136c23ca3e |
| redesign/lib/yoteiban_rd/__init__.py | ed569e6be5d8efe0247bb7626e9c0c7dd7b3ecd0 |
| redesign/lib/yoteiban_rd/calc.py | 82489dfee13abfcc63820653268d3ea193c4110a |
| redesign/lib/yoteiban_rd/cli.py | d0f22245ce7231dc31a4d04d18e7c88e5ec2ddbe |
| redesign/lib/yoteiban_rd/nightly.py | e126897b8d9097ebe6b9b2ceae9eaaa7d81037e6 |
| redesign/lib/yoteiban_rd/rules_slice.py | 9fb5b39ed0472664b3f991fe48b24eaff7bf242c |
| redesign/lib/yoteiban_rd/send_flow.py | 1ff242531b053f0a6b262b65ba7e4f0397661c01 |
| redesign/lib/yoteiban_rd/shadow.py | 3b9658ba016e41b3689aceb09cbee113995a1862 |
| redesign/lib/yoteiban_rd/urgency.py | fbef677ce40695e76dcdbccd34f2048ddedb3802 |
| redesign/mac/INSTALL.md | aecebd9326fc3739a257ff1dd9fe6b9964141236 |
| redesign/mac/config.example.json | fae0917fd9e54f324d2926bcdbdb1347dbecf1d7 |
| redesign/mac/launchd.plist.template | 3c13adb62af362f8b29350c828314ae2ad768168 |
| redesign/mac/yoteiban_watch/__init__.py | 2c9a4d593dd4f4da5afdd99b4a6b1ae392e92b98 |
| redesign/mac/yoteiban_watch/mask.py | 4253cc56d0df384ce97f9c7e5553f920b3ed62e7 |
| redesign/mac/yoteiban_watch/run.py | 65333bdd32ab349e43f5ac5a97a480b51aa3e815 |
| redesign/mac/yoteiban_watch/sorter.py | c9d8fb3dba7412cae763e6e8433517c73750dfe3 |
| redesign/mac/yoteiban_watch/sources.py | 62eb96a11850ed6d8e9a4f5e45d8f7c687df3d82 |
| redesign/mac/yoteiban_watch/store.py | 82583fb8c2c8f808385166db4c4653bb0e3b6329 |
| redesign/mac/yoteiban_watch/waker.py | b6aec8cc927a02defe4c54cce5dbf584361064f4 |
| redesign/skills/core/SKILL.md | 06664a571bad15fac6f3f478ed397a98a608ecf2 |
| redesign/skills/delegate/SKILL.md | 5c5002e7fcf0fbecb70f89c433e035521e8e99af |
| redesign/skills/intake/SKILL.md | 72ebdfb9be8f90c1b1d24e411d7657ccc2c7a4cf |
| redesign/skills/nightly/SKILL.md | ccbd43cc9db398f54119e344ca5a120345283ecf |
| redesign/skills/notify/SKILL.md | 557ae104be736f60b8defa9daacc8c8468fc5d97 |
| redesign/skills/write/SKILL.md | 592f594e756a12f8406bf99ed11c74d3e63eaf6d |
| redesign/tests/_paths.py | c4d30c9384587831c67fe8f344ad27de63adc4f2 |
| redesign/tests/test_calc_urgency.py | f6596c7f31044ba691d88f1a9a0185cd34cf9b4f |
| redesign/tests/test_cli.py | 7fabfffd9067903bea365c84b58f3888ca4350de |
| redesign/tests/test_mac.py | 8773219febfc177429529f589b8a8f01a4fce79d |
| redesign/tests/test_nightly_slice_shadow.py | 90dddf41a1cfa6398828678bf9b1e485e87b079d |
| redesign/tests/test_no_fixed_values.py | 17f87e1eb296a211ab1474f2dad48f4bf6cf4bfd |
| redesign/tests/test_send_flow.py | b056fac3a67185aa0fa588026b7016a3a5c79e95 |

## 入れないもの
- findings/（実データを含む模擬実行・調査メモ。箱の中だけ）
- .local_denylist.txt（実名の見張り用。箱の中だけ）
- __pycache__/

## 注意
- テストは tools/stage2/delegation.py を使う（redesign/../tools/stage2/）。seihon.md は redesign/../seihon.md を探し、無ければ環境変数 YRD_SEIHON か箱の写し。リポジトリで seihon.md の場所が違えば YRD_SEIHON を渡す。
- 実行：cd redesign && python3 -m unittest discover -s tests（51件）。
- PR の題の案：再設計版（完成形1）の試作を redesign/ に追加（本番は変えない）
