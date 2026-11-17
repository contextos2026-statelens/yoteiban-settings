# Git に入れるファイル一覧（再設計版）

置き場所：リポジトリ contextos2026-statelens/yoteiban-settings の **redesign/** の下に、この表の左の配置のまま置く。既存のファイル（tools/・skills/・seihon.md など）は変えない。

受け渡し場所：/cursor/stores/store-0500d7ab-2bc0-4e36-85d3-1bb7df0b5c79/redesign-handoff/（クラウドエージェントからは /cursor/stores/bot/redesign-handoff/）。git hash-object の値で照合する。

| リポジトリ内の場所 | git hash-object |
|---|---|
| redesign/DESIGN.md | 30375cdc5655698dca9c5b0a75ec487b8b46edf1 |
| redesign/README.md | d5df2996471cf1187fa92738bfd2f9a1f7f94bc2 |
| redesign/config/settings.example.json | 892d9b0dc97ab4881d9655427a846f136c23ca3e |
| redesign/lib/yoteiban_rd/__init__.py | ed569e6be5d8efe0247bb7626e9c0c7dd7b3ecd0 |
| redesign/lib/yoteiban_rd/calc.py | 82489dfee13abfcc63820653268d3ea193c4110a |
| redesign/lib/yoteiban_rd/cli.py | dcaae77637303cb8f91427604d4f07c56675188d |
| redesign/lib/yoteiban_rd/nightly.py | e126897b8d9097ebe6b9b2ceae9eaaa7d81037e6 |
| redesign/lib/yoteiban_rd/rules_slice.py | b67d21ee40be191bcf4646a71343f8aa97e161a2 |
| redesign/lib/yoteiban_rd/send_flow.py | 1ff242531b053f0a6b262b65ba7e4f0397661c01 |
| redesign/lib/yoteiban_rd/shadow.py | 3b9658ba016e41b3689aceb09cbee113995a1862 |
| redesign/lib/yoteiban_rd/urgency.py | 68ceff7fc495cb61b8a19c48031e2e121e599a6a |
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
| redesign/skills/core/SKILL.md | 8b568ad76863f7b735dbdb4eebfc9e4fa939eb65 |
| redesign/skills/delegate/SKILL.md | 5c5002e7fcf0fbecb70f89c433e035521e8e99af |
| redesign/skills/intake/SKILL.md | 5e9e55d35db88fc469f8192b8b5d0fdf56f86a36 |
| redesign/skills/nightly/SKILL.md | 0ba3414cca5aeee4c7e87ac23de39a754f9b302d |
| redesign/skills/notify/SKILL.md | 79346045206f39773f33e10a24133035c03c4e7f |
| redesign/skills/write/SKILL.md | dc04bf69647bddaecfd3037db4c37593a47b71f5 |
| redesign/tests/_paths.py | c4d30c9384587831c67fe8f344ad27de63adc4f2 |
| redesign/tests/test_calc_urgency.py | dad5184f264537d0835b176bd832cbd923e8d010 |
| redesign/tests/test_cli.py | 71f69377827a745ee76c80b50611eaff0bba93c2 |
| redesign/tests/test_mac.py | 8773219febfc177429529f589b8a8f01a4fce79d |
| redesign/tests/test_nightly_slice_shadow.py | e05ff87dd6718f14cabda4448f63ecd5cd4d0cac |
| redesign/tests/test_no_fixed_values.py | 62f07d068196dbb73125fd8f74f48ca3ebd22616 |
| redesign/tests/test_send_flow.py | b056fac3a67185aa0fa588026b7016a3a5c79e95 |

## 入れないもの
- findings/（実データを含む模擬実行・調査メモ。箱の中だけ）
- .local_denylist.txt（実名の見張り用。箱の中だけ）
- __pycache__/

## 注意
- テストは tools/stage2/delegation.py を使う（redesign/../tools/stage2/）。seihon.md は redesign/../seihon.md を探し、無ければ環境変数 YRD_SEIHON か箱の写し。リポジトリで seihon.md の場所が違えば YRD_SEIHON を渡す。
- 実行：cd redesign && python3 -m unittest discover -s tests（40件）。
- PR の題の案：再設計版（完成形1）の試作を redesign/ に追加（本番は変えない）
