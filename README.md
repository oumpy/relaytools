# RelayTools

原案・原作 AtamaokaC

Mattermost／Slack上のリレー投稿を支援するBotスクリプト類です。
現在までに以下のものが作成されています。

## RelayReminder

Python会リレー投稿支援システム、第3世代。
2023年9月より稼働。

個人ごとにリレー投稿履歴をチャンネル上でチェックし、前回投稿から決められた週数が近づくと、お知らせを投稿する。
週1回、cronで実行すればよい。

同時に、常駐サービスとして稼働してスラッシュコマンド `/whenmylast` を提供する機能を備える。
任意のチャンネル上で実行すると、自分の最終投稿日時を知ることができる。

設定は `$HOME/.relayreminder/env` を読み込む。
`env_jp_example` ファイルを参照。

## RelayScheduler

2020年3月より、Python会Slackで運用中。
2022年10月、Mattermostに移管。

来週のリレー投稿担当者を順番に選んでMattermost／Slackに投稿する。
週ごとにファイルに保存。
指定したチャンネルのメンバーを「IDのsha256」の順で並べる。
履歴の最後を取り出して、その次にあたる人から順番。
履歴ファイルの番号は拡張グレゴリオ歴1年1月1日（月）を第0週とした週番号である。
すでにファイルがある場合はリマインドする。
cronで呼び出して使うのが基本。

対応Pythonバージョン：3.9以上

（詳しくはあとで書く、かもしれない。）

### オプション

- `--system`：'mattermost' or 'slack'
- `--local`：mattermost/slackに投稿せず投稿用メッセージを画面に出力。
- `-r`, ` --reminder`：新規アナウンスではなく直近のリマインダを投稿。
- `--mute`：スレッド内投稿でチャンネルには表示しない。
- `--solopost`：スレッドを使わず単独メッセージとして投稿。従来のスレッドはそのまま。
- `--list`：メンバー全員の次回アナウンスからの順番を投稿。
- `--skipholiday`：祝休日はスキップする。日本の祝日（jpholidayパッケージ利用）のほか12/24-1/3を年末年始休暇に設定。
- `--showcycle`：新しい巡回に入る際、その旨と何巡目に入るかを表示する。
- `-c`, `--channnel`：読み込み・投稿するチャンネルを指定する。
- `-o`, `--outchannnel`：投稿するチャンネルを指定する。
- `--token`：BotのTokenを指定する。
- `--tokenfile`：BotのTokenが記されたファイルを指定する。
- `--date`：今日でなく任意の日付を指定する。
- `--exclude`：追加の除外ユーザリストファイルを指定する。リストを渡すこともできる。
- `--id-dictionary`：IDの置換表ファイルを指定する。

## WhoActive

Slack上のアクティブユーザを確認・更新する。

現在のログイン状態やリレー投稿履歴を確認し、各々設定した期間内にそれらが確認できなかった者を休眠ユーザと認定。  
生成された休眠ユーザリストは、RelayScheduler に `--exclude` オプションでそのまま渡すことができる。  
認定および解除の際、DMや指定チャンネルに通知することもできる。

### オプション

- `--checkpresence` : メンバーのログイン状態を確認
- `--checkrelay` : リレー投稿を確認
- `--show` : 最近アクティブ状態を確認した日時を表示
- `--showrelay` : 最後にリレー投稿した日時を表示
- `--updatealive` : メンバーの休眠状態を更新
- `--judgedead` : `updatealive`の際、長期リレー投稿なしによる「死亡」を判定
- `--notify` : 休眠／生死の更新があった場合本人にDM通知
- `--postlog` : 休眠／生死の更新があった場合チャンネルに投稿
- `--channel` : `postlog`のチャンネル指定
- `--relaychannel` : リレー投稿のチャンネル指定
- `--touch <ID>` : `<ID>`で指定したユーザがアクティブ状態であったと（虚偽の）記録を行う
- `--slacktoken` : Botのトークンを指定する


## RelayAdvisor (v1.0)

指名制リレー投稿用。
Python会Slackでは2020年2月までで運用停止。

RelayAdvisorは、Slackチャンネル上でのリレー投稿（次の人の指名）を支援するBotアプリです。

Botをメンションして投稿すると、チャンネルのメンバー（投稿者以外）からランダムに1人を選んで提案の返信を行います。

### Installation

- https://api.slack.com/apps?new_granular_bot_app=1 でアプリを作成し、適切に設定。作成したBotを目的のチャンネルに追加しておく。

- 本スクリプト`relayadvisor.py`と同じディレクトリに`slack_token`という名前のファイルを置き、その1行目にBot Tokenを書き込む。

あとは Python3 (>= 3.6) で実行するだけです。SlackにアクセスできればグローバルIPは不要です。  
（クラッシュに備え、自動で再起動するよう設定することなどをお勧めします。）
