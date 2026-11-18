# Git に入れるファイル一覧（再設計版）

置き場所：リポジトリ contextos2026-statelens/yoteiban-settings の **redesign/** の下に、この表の左の配置のまま置く。tools/・rules/seihon.md は変えない。1.0.22 からは skills/ も新しい本文に入れ替える（本番への切り替えは1本ずつ、別の手順で行う）。

受け渡し場所：ボット固有ストアの redesign-handoff/redesign/。git hash-object の値で照合する。

ファイル数：45（この表）＋ GIT_MANIFEST.md ＝ 46

| リポジトリ内の場所 | git hash-object |
|---|---|
| redesign/DESIGN.md | 0e7c70c678a26d9b4fd13a06513e28f48bf1feca |
| redesign/README.md | d5df2996471cf1187fa92738bfd2f9a1f7f94bc2 |
| redesign/config/settings.example.json | 84541fd172eb155488b35bf94d764ef821382695 |
| redesign/docs/R3_MAPPING.md | 12cef9c1a01b528af3f173e9fc338df18cb517f9 |
| redesign/lib/yoteiban_rd/__init__.py | ed569e6be5d8efe0247bb7626e9c0c7dd7b3ecd0 |
| redesign/lib/yoteiban_rd/calc.py | 995a4d2e22a17fa570efc76f72fb23e1d8aadb38 |
| redesign/lib/yoteiban_rd/cli.py | e05bc04761a5a94931481da820505fc25e66b420 |
| redesign/lib/yoteiban_rd/household.py | 82ee296c6cf1ac5979ba9a09e5212315cc55d829 |
| redesign/lib/yoteiban_rd/morning.py | 0d8d697a1578f884ec644f8ac4c0aee8d4bc4a9f |
| redesign/lib/yoteiban_rd/nightly.py | 57bc725ed4389e246afa693219299994743151db |
| redesign/lib/yoteiban_rd/rules_slice.py | 6c33c9e537ae739f07719972ef502d1e3ef95864 |
| redesign/lib/yoteiban_rd/selfcheck.py | d8165633fa406406d8c64101245bb7fae9332d1f |
| redesign/lib/yoteiban_rd/send_flow.py | 1ff242531b053f0a6b262b65ba7e4f0397661c01 |
| redesign/lib/yoteiban_rd/shadow.py | 3b9658ba016e41b3689aceb09cbee113995a1862 |
| redesign/lib/yoteiban_rd/urgency.py | 11ba8f5004dbe720beedf400dff618a2c53f004b |
| redesign/mac/INSTALL.md | aecebd9326fc3739a257ff1dd9fe6b9964141236 |
| redesign/mac/config.example.json | c96523c8eb0d14e3c01deebd4195c94cfe5709fb |
| redesign/mac/launchd.plist.template | 3c13adb62af362f8b29350c828314ae2ad768168 |
| redesign/mac/yoteiban_watch/__init__.py | 2c9a4d593dd4f4da5afdd99b4a6b1ae392e92b98 |
| redesign/mac/yoteiban_watch/mask.py | 1b98753536f834a27c426602d325a2f393bba337 |
| redesign/mac/yoteiban_watch/run.py | 4fcf1924a673b8c92b55e3179b0da4b3cf871174 |
| redesign/mac/yoteiban_watch/sorter.py | d8e55a4edd625450f15fd98f6d51e57e039bcb9c |
| redesign/mac/yoteiban_watch/sources.py | 62eb96a11850ed6d8e9a4f5e45d8f7c687df3d82 |
| redesign/mac/yoteiban_watch/store.py | 82583fb8c2c8f808385166db4c4653bb0e3b6329 |
| redesign/mac/yoteiban_watch/waker.py | b6aec8cc927a02defe4c54cce5dbf584361064f4 |
| redesign/skills/adjust/SKILL.md | 7773d766b91c185621b55fb6b91095ff6214f0a9 |
| redesign/skills/common/SKILL.md | 766034e2ea15b6fc667012d2ce49dc10fbd54585 |
| redesign/skills/delegate/SKILL.md | 19533dfd8ed398c9887a3b17060291a4c6992d26 |
| redesign/skills/infer/SKILL.md | 0dba902c5b72b5d9da13dcd55c7a3e1749eeb29f |
| redesign/skills/intake/SKILL.md | 685634bfdf222e98620f822ec4ea9154696827d4 |
| redesign/skills/interval/SKILL.md | bdb5695a6ef4de7e2ccddebf0496acabe68c02f8 |
| redesign/skills/nightly/SKILL.md | ceb048e5deeab217d7aaf1665e756b0a7dd9a948 |
| redesign/skills/notify/SKILL.md | a4a7ba6bb2bd97c435159dcddef32a0d5f8b7b78 |
| redesign/skills/selftest/SKILL.md | f05436425a51f68d0a458eb347388a66a4d4626b |
| redesign/skills/setup/SKILL.md | 47fca09c478e1aa43411b8b81ba8ac7b0d935528 |
| redesign/skills/write/SKILL.md | 4e0bbd897f720f39e3871f00d6e1d0d712352d7c |
| redesign/tests/_paths.py | c4d30c9384587831c67fe8f344ad27de63adc4f2 |
| redesign/tests/test_adversarial.py | 44e9bbbf05ff46c822f3229066ff2c500cfa9a2d |
| redesign/tests/test_calc_urgency.py | f6596c7f31044ba691d88f1a9a0185cd34cf9b4f |
| redesign/tests/test_cli.py | bc69a5221c6143ea4e7370922ba4d5137a632993 |
| redesign/tests/test_mac.py | 8773219febfc177429529f589b8a8f01a4fce79d |
| redesign/tests/test_nightly_slice_shadow.py | 70eb549ceabb02d00bdb7c8b2c99e2ed8d9101c7 |
| redesign/tests/test_no_fixed_values.py | e7f675f9f3d3ce83713ed031b574f2acc5c7c68a |
| redesign/tests/test_safety_rules.py | 4d7565e83e1c6e87e2d6719e79ff17415c00990f |
| redesign/tests/test_send_flow.py | b056fac3a67185aa0fa588026b7016a3a5c79e95 |

## 入れないもの
- findings/（実データを含む模擬実行・調査メモ。箱の中だけ）
- .local_denylist.txt（実名の見張り用。箱の中だけ）
- __pycache__/

## 注意
- テストは tools/stage2/delegation.py を使う（redesign/../tools/stage2/）。seihon.md は redesign/../seihon.md を探し、無ければ環境変数 YRD_SEIHON か箱の写し。リポジトリで seihon.md の場所が違えば YRD_SEIHON を渡す。
- 実行：cd redesign && python3 -m unittest discover -s tests（51件）。
- PR の題の案：再設計版（完成形1）の試作を redesign/ に追加（本番は変えない）
