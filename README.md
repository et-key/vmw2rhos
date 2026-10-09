# vmw2rhos

vSphereからRed Hat OpenShift Virtualizationへの移行を、表の編集から計画するツールです。
使いやすさを優先し、ストレージ、ネットワーク、Namespace、Affinity、OS更新などの例外を同じ計画にまとめます。

## 起動

Python 3.12以上を使用します。追加パッケージのインストールは不要です。

```powershell
python -m vmw2rhos
```

ブラウザーで `http://127.0.0.1:8765` を開きます。終了はターミナルで Ctrl+C。
ポートと保存先は変更できます。

```powershell
python -m vmw2rhos --port 8766 --data .vmw2rhos/plans.sqlite3
```

## 現在できること

- JSONで構成を取り込み、差分を確認して表へ反映。
- VM、共有・外部ストレージ、NIC/VLAN、配置ルール、例外処理、共通対応の編集。
- 検索、複数行の選択、一括変更、JSONの書出し。
- 個別設定を優先したNamespace・StorageClass・ネットワークの共通対応。
- 必須設定、参照、容量、共有領域のNamespaceと切替グループ、VLAN、依存循環のチェック。
- SQLiteへの版保存、古い版を元にした上書きの拒否、対応ルールの出所と移行順序を含む計画出力。

「サンプルで始める」でOS混在・外部共有領域・Affinity・OS更新を含む構成を編集できます。
サンプルには意図的に未確認・未設定項目を含めています。サンプルのチェック状態は実環境の証跡ではありません。
JSON形式の参考は [サンプル](vmw2rhos/web/sample.json) を参照してください。
IDは接続先と移行元の安定した識別子を組み合わせて指定します。
この版では共通Namespace対応はフォルダーの完全一致です。タグ条件や既定値の対応は未実装です。

## 実装範囲

現在は設計書の第一段階にあたるローカルの計画作成版です。
vSphere自動収集、OpenShift接続、実転送、OS更新の実行、ライブ事前検証、切り戻しは未実装です。
チェックで入力が整合しても `executable: false` とし、実行可能とは判定しません。
領域や配置ルールの「確認済み」は入力内容に対する利用者の確認です。
Warm/Coldの対応可否、実容量、ネットワーク到達性などは実環境のアダプターで確認する必要があります。

ブラウザーを閉じても保存済みの版は残ります。未保存の変更はブラウザーのみに保持されます。
保存先の `.vmw2rhos/` はGit管理外です。計画を共有する際はJSONを書き出してください。
認証情報はJSONに入力しないでください。この版はローカルの単独利用を対象とし、認証・複数利用者の権限管理は未実装です。

## 開発・検証

```powershell
python -m unittest discover -s tests -v
node --check vmw2rhos/web/app.js
```

設計と受入条件は [設計文書](docs/spec/migration-tool-design.md) に記載しています。
