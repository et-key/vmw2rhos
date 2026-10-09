"""Pure planning logic; never performs operations on migration endpoints."""

from copy import deepcopy
import re

TABLES = ('vms', 'volumes', 'nics', 'placements', 'actions', 'rules')
FIELDS = {
    'vms': {'id': str, 'name': str, 'os': str, 'folder': str, 'namespace': str,
            'target_name': str, 'mode': str, 'group': str, 'dependencies': list},
    'volumes': {'id': str, 'vms': list, 'source': str, 'kind': str, 'purpose': str,
                'capacity_gib': int, 'target_gib': int, 'strategy': str,
                'storage_class': str, 'endpoint': str, 'access': str,
                'consistency': str, 'verified': bool},
    'nics': {'id': str, 'vm': str, 'source': str, 'source_vlan': int,
             'target_network': str, 'target_vlan': int, 'mac_policy': str,
             'ip_policy': str, 'verified': bool},
    'placements': {'id': str, 'vms': list, 'source_rule': str,
                   'target_rule': str, 'verified': bool},
    'actions': {'id': str, 'vm': str, 'kind': str, 'phase': str,
                'definition': str, 'success_condition': str, 'failure_policy': str,
                'verified': bool},
    'rules': {'id': str, 'kind': str, 'source': str, 'target': str},
}
ENUMS = {
    ('vms', 'mode'): ('warm', 'cold'),
    ('volumes', 'kind'): ('virtual-disk', 'external', 'shared', 'special'),
    ('volumes', 'strategy'): ('copy', 'reuse', 'reconnect', 'manual'),
    ('volumes', 'access'): ('single-writer', 'multi-writer', 'read-only'),
    ('nics', 'mac_policy'): ('preserve', 'regenerate'),
    ('nics', 'ip_policy'): ('preserve', 'change'),
    ('actions', 'kind'): ('os-upgrade', 'driver-change', 'script', 'manual'),
    ('actions', 'phase'): ('before', 'before-cutover', 'after'),
    ('actions', 'failure_policy'): ('stop', 'manual'),
    ('rules', 'kind'): ('namespace', 'network', 'storage'),
}


def empty_inventory():
    return {'schema_version': 1, **{table: [] for table in TABLES}}


def validate_inventory(data):
    """Reject malformed imports before they can replace persisted work."""
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('schema_version は整数の1を指定してください。')
    if set(data) != {'schema_version', *TABLES}:
        raise ValueError('構成データの項目が不足しているか、未知の項目があります。')
    for table in TABLES:
        rows = data[table]
        if not isinstance(rows, list):
            raise ValueError(f'{table} は配列で指定してください。')
        ids = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(FIELDS[table]):
                raise ValueError(f'{table}: 列が不足しているか、未知の列があります。')
            for key, expected in FIELDS[table].items():
                value = row[key]
                # VLAN unknown is explicit null, distinct from untagged (0).
                if table == 'nics' and key in ('source_vlan', 'target_vlan') and value is None:
                    continue
                if type(value) is not expected:
                    raise ValueError(f'{table}.{key}: 型が不正です。')
                limit = 1_000_000 if table == 'placements' and key == 'source_rule' else 8192
                if expected is str and len(value) > limit:
                    raise ValueError(f'{table}.{key}: 値が長すぎます。')
                if expected is list and (any(type(v) is not str or not v for v in value) or len(set(value)) != len(value)):
                    raise ValueError(f'{table}.{key}: 重複のないID配列を指定してください。')
                if expected is int and value < 0:
                    raise ValueError(f'{table}.{key}: 負数は指定できません。')
                options = ENUMS.get((table, key))
                if options and value not in options:
                    raise ValueError(f'{table}.{key}: {options} から選択してください。')
            if not row['id'] or row['id'] in ids:
                raise ValueError(f'{table}: IDが空または重複しています。')
            ids.add(row['id'])
    return deepcopy(data)


def build_plan(inventory):
    data = validate_inventory(inventory)
    issues = []
    provenance = []

    def issue(table, row, field, code, message, remedy):
        issues.append(dict(table=table, id=row, field=field, code=code,
                           message=message, remedy=remedy, severity='blocking'))

    def resolve(table, row, field, kind, source):
        explicit = row[field]
        if explicit:
            value, origin = explicit, 'individual'
        else:
            matches = [r for r in data['rules'] if r['kind'] == kind and r['source'] == source]
            if len(matches) > 1:
                issue(table, row['id'], field, 'rule-conflict', '共通ルールが競合しています。', '同じ条件のルールを一つにしてください。')
                value, origin = '', 'conflict'
            elif matches:
                value, origin = matches[0]['target'], matches[0]['id']
            else:
                value, origin = '', 'unresolved'
        row[field] = value
        provenance.append(dict(table=table, id=row['id'], field=field, value=value, origin=origin))
        return value

    def required(table, row, field, message):
        if not row[field].strip():
            issue(table, row['id'], field, 'required', message, '値を入力するか共通対応ルールを設定してください。')

    def dns_label(value):
        return len(value) <= 63 and re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', value)

    vm_ids = {v['id'] for v in data['vms']}
    vm_by_id = {v['id']: v for v in data['vms']}
    if not vm_ids:
        issue('vms', '', '', 'empty', '移行対象がありません。', '構成データを取り込んでください。')
    destinations = set()
    for vm in data['vms']:
        resolve('vms', vm, 'namespace', 'namespace', vm['folder'])
        for field in ('namespace', 'target_name', 'os', 'name'):
            required('vms', vm, field, f'{field} が未入力です。')
        for field in ('namespace', 'target_name'):
            if vm[field] and not dns_label(vm[field]):
                issue('vms', vm['id'], field, 'invalid-name', '移行先の名前が不正です。', '63文字以内の英小文字・数字・ハイフンを使用してください。')
        destination = (vm['namespace'], vm['target_name'])
        if destination in destinations:
            issue('vms', vm['id'], 'target_name', 'duplicate-target', '移行先VM名が重複しています。', 'NamespaceまたはVM名を変更してください。')
        destinations.add(destination)
        for dependency in vm['dependencies']:
            if dependency not in vm_ids:
                issue('vms', vm['id'], 'dependencies', 'missing-reference', f'依存VM {dependency} が存在しません。', '対象VMを追加するか参照を修正してください。')

    def references(table, row, refs):
        if not refs:
            issue(table, row['id'], 'vms', 'missing-reference', '接続するVMが指定されていません。', 'VMのIDを指定してください。')
        for ref in refs:
            if ref not in vm_ids:
                issue(table, row['id'], 'vm', 'missing-reference', f'VM {ref} が存在しません。', '参照するVMのIDを修正してください。')

    for volume in data['volumes']:
        references('volumes', volume, volume['vms'])
        if volume['strategy'] == 'copy':
            resolve('volumes', volume, 'storage_class', 'storage', volume['source'])
            required('volumes', volume, 'storage_class', 'StorageClassが未設定です。')
            if volume['target_gib'] < volume['capacity_gib'] or volume['target_gib'] == 0:
                issue('volumes', volume['id'], 'target_gib', 'capacity', '割当容量が不足しています。', '元の容量以上を指定してください。縮小移行は未対応です。')
        elif volume['strategy'] in ('reuse', 'reconnect'):
            required('volumes', volume, 'endpoint', '継続利用・再接続の接続先が未設定です。')
        else:
            issue('volumes', volume['id'], 'strategy', 'manual', '手動移行が必要です。', '対応手順を定め、移行方式を確認してください。')
        if volume['kind'] in ('external', 'special') and volume['strategy'] == 'copy':
            issue('volumes', volume['id'], 'strategy', 'unsupported-copy', '外部・特殊領域の汎用コピーは未対応です。', '継続利用・再接続か、手動対応を計画してください。')
        if len(volume['vms']) > 1:
            related = [vm_by_id[x] for x in volume['vms'] if x in vm_by_id]
            if volume['strategy'] == 'copy' and len({v['namespace'] for v in related}) > 1:
                issue('volumes', volume['id'], 'vms', 'shared-namespace', '共有コピー領域のNamespaceが異なります。', '同一Namespaceに配置するか、共有方式を見直してください。')
            if any(not v['group'] for v in related) or len({v['group'] for v in related}) > 1:
                issue('volumes', volume['id'], 'vms', 'shared-group', '共有領域の切替グループが一致していません。', '関連VMを同じ移行グループにしてください。')
            required('volumes', volume, 'consistency', '共有領域の整合性確保手順が未入力です。')
        if not volume['verified']:
            issue('volumes', volume['id'], 'verified', 'unverified', '領域の構成・アクセス要件が未確認です。', '接続、用途、容量、共有方式を確認してください。')
    for vm in data['vms']:
        if not any(vm['id'] in v['vms'] for v in data['volumes']):
            issue('vms', vm['id'], 'volumes', 'missing-disk', 'ディスク情報がありません。', 'OS・データ領域を登録してください。')
    for nic in data['nics']:
        references('nics', nic, [nic['vm']])
        resolve('nics', nic, 'target_network', 'network', nic['source'])
        required('nics', nic, 'target_network', '移行先ネットワークが未設定です。')
        for field in ('source_vlan', 'target_vlan'):
            if nic[field] is not None and nic[field] > 4094:
                issue('nics', nic['id'], field, 'vlan-range', 'VLANは0〜4094の範囲です。', '0はタグなし、未確認はnullとしてください。')
        if nic['target_vlan'] is None or not nic['verified']:
            issue('nics', nic['id'], 'verified', 'unverified-network', '移行先VLAN・接続構成が未確認です。', 'ネットワーク定義、経路、IP変更方針を確認してください。')
    for table in ('placements', 'actions'):
        for row in data[table]:
            references(table, row, row['vms'] if table == 'placements' else [row['vm']])
            fields = ('target_rule',) if table == 'placements' else ('definition', 'success_condition')
            for field in fields:
                required(table, row, field, f'{field} が未設定です。')
            if not row['verified'] or (table == 'actions' and row['kind'] == 'manual'):
                issue(table, row['id'], 'verified', 'unverified-action', '変換・処理の確認が必要です。', '対応方法と成功判定を確認してください。')
    order, pending = [], set(vm_ids)
    while pending:
        ready = sorted(x for x in pending if not (set(vm_by_id[x]['dependencies']) & pending))
        if not ready:
            issue('vms', '', 'dependencies', 'dependency-cycle', 'VMの依存関係に循環があります。', '依存関係を見直してください。循環グループの実行は未対応です。')
            break
        order.extend(ready)
        pending.difference_update(ready)
    return dict(schema_version=1, inventory=data, provenance=provenance,
                order=order, issues=issues, locally_valid=not issues,
                executable=False,
                live_checks=['OS・Warm/Cold対応', '接続・権限・Quota', 'StorageClassの能力と実容量',
                             'ネットワーク到達性', 'Affinityの実現可否', '処理・復旧手段の実行確認'],
                status='要修正' if issues else 'ローカルチェック完了・実環境確認待ち')

