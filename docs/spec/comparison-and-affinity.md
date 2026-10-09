# 移行差分とAffinity自動収集

更新日: 2026-10-09

## 目的

移行元と移行先の単純な差分だけでは、意図した変更をミスと区別できない。
「移行元」「共通対応を適用した計画」「移行先の実測」の3列で比較する。
Namespace、ネットワーク、容量などを変更しても、実測が計画と一致すれば意図した変更と判定する。

## 比較対象と判定

| 対象 | 項目 |
| --- | --- |
| VM | 名前、配置先（元フォルダー→移行先Namespace）、OS |
| 領域 | 容量、対応先（元領域→StorageClassまたは外部接続先）、アクセス要件、関連VM |
| NIC | 接続先ネットワーク、VLAN、接続VM |
| 配置ルール | 元ルール、計画したルール、関連VM |

判定は、計画上維持、計画した変更、計画未確定、実測未取得、対象欠落、計画にない対象、計画と不一致、計画と一致、計画した変更と一致、別の計画の実測を区別する。
0のVLANと未取得を区別する。関連VMの順序やJSONオブジェクトのキー順序だけでは不一致にしない。

OS更新の移行先バージョンは現行の計画形式にないため、OS更新があるVMの比較先OSは未確定とする。
OSの版を推測して一致扱いにしない。CPU、メモリ、実MAC・IP、DNS、経路、マウント、データ整合性の比較は次の段階で追加する。
配置ルールは構造または文字列の比較であり、vSphereとOpenShiftの配置制約が意味的に等価であることは自動判定しない。

## 実測の交換形式

画面から「移行先実測テンプレート」をダウンロードし、移行先で確認した値を入力して取り込む。
テンプレートの実測値はすべてnullとする。計画値を実測値として自動記入しない。
captured_atはタイムゾーン付きISO 8601の取得日時、plan_fingerprintはテンプレートに入った値を保持する。

```json
{
  "schema_version": 1,
  "captured_at": "2026-10-09T23:30:00+09:00",
  "plan_fingerprint": "テンプレートに入った計画のSHA-256",
  "resources": [
    {
      "table": "nics",
      "id": "元の接続先ID:VM-ID:NIC-ID",
      "values": {"network": "business-net", "vlan": 120, "vm": "元の接続先ID:VM-ID"}
    }
  ]
}
```

リソースは移行元の安定したIDで対応付ける。名前による推測は行わない。
値がnullまたは項目が省略されている場合は未取得、対象自体がない場合は欠落として区別する。
計画識別子が異なる実測は古い実測として扱い、一致判定に使用しない。
SHA-256は取り違え防止用であり、実測の真正性を証明する署名ではない。

実測は現在ブラウザー内で保持する。比較結果はJSONで書き出せる。
OpenShiftからの実測自動収集は未実装。観測項目がすべて一致しても、通信・データ・アプリケーション検証の代替にはならないため、migration_verifiedはfalseとする。
実測を取り込まない比較は計画比較であり、移行結果の検証ではない。

## vSphere Affinityの収集

pyVmomiを使用し、クラスタのconfigurationEx.ruleとconfigurationEx.groupを読取り専用で取得する。

- VM同士のAffinity／Anti-Affinityの対象VM。
- VM–ホストルールのVMグループ、Affinity／Anti-Affinityホストグループとそのメンバー。
- 有効状態、必須・推奨の属性、キー、UUID、ルール名、状態、クラスタ、取得日時。

無効ルールも収集し、取得できないグループやVM参照は未解決として残す。
未知のルール型は共通属性を保存し、専用属性が未収集であることを警告する。
vSphereホストからOpenShiftノードへの対応は自動で推測しない。
収集したルールは既存のplacements.source_ruleにJSONとして保存し、target_ruleは空、verifiedはfalseにする。
SDKのinComplianceは参考属性として保存するが、配置適合の検証結果として利用しない。

取得にはvCenter証明書を検証するTLS接続を使う。独自CAは--caで指定する。
パスワードは対話入力または専用環境変数から取得し、JSONとログに保存しない。
接続の終了とContainerViewの解放は例外時も実行する。

この収集処理はVMの基本情報と配置ルールを取得する。ディスク・NIC・ゲスト内部の調査は未実装であり、出力だけでは実行可能な計画にならない。
取り込み時は差分を確認し、既存の編集がある場合は書き出して保存する。再収集結果と個別設定の自動マージは未実装。

## APIと実環境検証

- POST /api/compare: inventoryと任意のobservationを比較する。
- POST /api/observation-template: 計画識別子付きの空の実測テンプレートを生成する。
- CLI: python -m vmw2rhos.collect_vsphere。

テストではルール抽出、グループ参照、TLS設定、終了処理と差分判定を確認する。
実vCenterの接続情報がないため、権限、製品バージョン、実環境のルール取得は未検証。

## 参照した一次資料

- [ClusterConfigInfoEx](https://developer.broadcom.com/xapis/vsphere-web-services-api/latest/vim.cluster.ConfigInfoEx.html): クラスタのruleとgroup。
- [ClusterRuleInfo](https://developer.broadcom.com/xapis/vsphere-web-services-api/latest/vim.cluster.RuleInfo.html): enabled、mandatory等の属性。
- [ClusterVmHostRuleInfo](https://developer.broadcom.com/xapis/vsphere-web-services-api/latest/vim.cluster.VmHostRuleInfo.html): VM・ホストグループの参照。
- [pyVmomi接続処理](https://github.com/vmware/pyvmomi/blob/master/pyVim/connect.py): SmartConnectとDisconnect。
