# Mac mini の見張り番 導入手順（まだ置かない・動かさない）

この手順は、本人の承認の後に初めて行う。伝書鳩（今の常駐）には触らない。名前も場所も別にする。

## 0. 前もって必要なこと（本人の操作）

- Gmail・カレンダー：Google を読む道具（gog）に、読むだけの権限でログインする（アカウントごと）。ログイン済みが0件のあいだは、iMessage だけで動かす。
- 外部AIの鍵（使うときだけ）：環境ファイルに置く。月の上限（円）を本人が決める。
- iMessage の記録を読むには、実行する python3 に「フルディスクアクセス」が要る（伝書鳩で既に許可済みかを確かめる）。

## 1. 置く（ファイルだけ）

```
mkdir -p ~/yoteiban-watch/{app,state}
# リポジトリの redesign/mac/yoteiban_watch を ~/yoteiban-watch/app/ に写す
cp config.example.json ~/yoteiban-watch/config.json   # 中身を本物の値に直す（Git には入れない）
printf 'YOTEIBAN_WEBHOOK_URL=\nYOTEIBAN_WEBHOOK_KEY=\nYOTEIBAN_AI_KEY=\n' > ~/yoteiban-watch/env
chmod 600 ~/yoteiban-watch/env ~/yoteiban-watch/config.json
```

- `config.json` の `shadow` は最初 `true`（起こさない・記録だけ）。並べて試す期間が終わり、本人の承認があってから `false`。
- `start_rowid` は置く時点の最新の番号にする（過去の分を一度に起こさない）。

## 2. 1回だけ試す（起こさない）

```
cd ~/yoteiban-watch/app && /usr/bin/python3 -m yoteiban_watch.run --config ~/yoteiban-watch/config.json --once --shadow
tail ~/yoteiban-watch/state/watch.log.jsonl
```

## 3. 常駐にする

`launchd.plist.template` の `{LABEL}`・`{HOME}`・`{PY}` を置き換えて `~/Library/LaunchAgents/` に置き、`launchctl bootstrap gui/$(id -u) <plist>`。

## 止める・戻す

- 止める：`launchctl bootout gui/$(id -u)/<LABEL>`。
- 戻す：上の plist と `~/yoteiban-watch` を消すだけ。ほかの仕組み（伝書鳩・予定ばんの定期の見回り）はそのまま動く。
- 止まっている間に届いた分は、予定ばんの定期の見回りが照合で拾う。

## 守り

- 読むだけ：chat.db は読み取り専用で開く。Google の道具は `--enable-commands` で gmail か calendar の1つに絞り、送信・変更の語が入った呼び出しは道具を呼ぶ前に止める。
- Webhook に本文は入れない（依頼ID・入口の参照・仕分けの結果だけ）。
- 外部AIに渡すのは、伏せ字にした本文と送り主の役割だけ。履歴・写真・名前・番号・アドレスは渡さない。
- 外部AIの答えは決まった形以外は捨て、決まりだけの仕分けに落とす。月の上限に達したら外部AIを止める。
