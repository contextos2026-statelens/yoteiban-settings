# Mac miniへの導入・停止・削除

この手順は本人が承認した後に実行します。見張り番と時計は別々のlaunchdサービスとして動きます。どちらも専用の `com.yoteiban.*` 名と `~/yoteiban-watch` の場所を使い、既存の伝書鳩には触れません。

## 導入前

- `python3` を用意します。iMessageを読む場合は、フルディスクアクセスはlaunchdで実際に動くPython（`status.sh` または `ps` で見える実体）に付けます。HomebrewのPythonは更新で場所が変わり許可が外れることがあるので、更新後は `status.sh` と `watch.log.jsonl` の `unreadable` を確かめます。
- Gmail・カレンダーは読み取り専用で接続します。外部AIは既定で無効です。
- 通知先が署名を検証するよう設定します。署名は送信JSON本文のHMAC-SHA256で、鍵は `YOTEIBAN_WEBHOOK_KEY` に置きます。
- Grok Botの受け口のようにAuthorizationヘッダーで鍵を受ける先には `YOTEIBAN_WEBHOOK_AUTH` を使います。
- Grok Botの受け口へは `YOTEIBAN_WEBHOOK_FORMAT=envelope` を設定します。`YOTEIBAN_WEBHOOK_AUTH` には送り手の鍵だけを書けばBearerは自動で付きます。`env` は `sh` で読むため、空白を含む値は必ず `"..."` で囲みます。
- `heartbeat_url`、`clock_heartbeat_url`、`due_url`、`webhook_url` は本人が用意した値を `config.json` に設定します。Git管理下の例には実値を入れません。

## インストール

リポジトリの `redesign/mac` から次を実行します。

```sh
bash scripts/install.sh
```

スクリプトは `~/yoteiban-watch/app` にプログラム、`~/yoteiban-watch/state` に状態を置きます。設定例は `config.json` として初回だけコピーし、環境変数ファイル `env` も初回だけ作ります。既存の設定は上書きしません。`config.json` と `env` は所有者だけが読める権限にします。

`config.json` で最初は `shadow: true` にします。`~/yoteiban-watch/env` にWebhook URLと鍵を記入し、必要なら外部AI鍵も設定します。鍵の値はplistに書きません。

## 影の運用から本番へ

1. `bash scripts/status.sh` で見張り番・時計の状態、最後の心拍、pending件数を確認します。
2. `~/yoteiban-watch/state/watch.log.jsonl` と心拍先を確認し、影の運用で読み取りと通知判定を確かめます。shadow中はWebhookを呼びません。
3. 本人が切り替えを承認した後、`config.json` の `shadow` を `false` にします。
4. 通知先がHMAC署名とrequest_idを検証し、重複を防ぐことを確認します。サービスは設定ファイルを次回起動時に読みます。再起動する場合は下記の停止・起動手順を使います。

時刻の確認は本文を取得せず、`due_url` から案件IDと確認時刻だけを読みます。見張り番は `heartbeat_url`、時計は `clock_heartbeat_url` に毎回GETします。心拍失敗時は各URLの末尾に `/fail` を付けてGETします。

## 状態確認・停止・再開

状態を確認します。

```sh
bash scripts/status.sh
```

停止する場合は2サービスだけを停止します。

```sh
launchctl bootout "gui/$(id -u)/com.yoteiban.watch" || true
launchctl bootout "gui/$(id -u)/com.yoteiban.clock" || true
```

再開する場合はインストールスクリプトを再実行します。設定ファイルと環境ファイルは保持されます。

## アンインストール

```sh
bash scripts/uninstall.sh
```

見張り番と時計を停止し、専用plist、アプリ、設定、環境ファイルを削除します。`~/yoteiban-watch/state` は削除しません。伝書鳩や他のサービスには触れません。

## データの扱い

- メール・メッセージ・カレンダーは読み取りだけです。
- Webhookには起床に必要な本文を送ります。`watch.log.jsonl` には本文を書かず、request_id・情報源・状態・文字数だけを記録します。
- 外部AIを使う場合、伏せ字にした本文だけを渡します。`ai.enabled` は初期値 `false` のままです。
- 位置情報は記録しません。
