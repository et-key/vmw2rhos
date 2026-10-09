"""Read-only vSphere DRS discovery. Optional pyVmomi dependency."""

import argparse
from datetime import datetime, timezone
from getpass import getpass
import json
import os
from pathlib import Path
import ssl
import sys

from .planner import empty_inventory, validate_inventory


def moid(obj):
    return obj._moId


def folder_path(vm):
    parts, current, seen = [], getattr(vm, 'parent', None), set()
    while current is not None and moid(current) not in seen:
        seen.add(moid(current))
        parts.append(current.name)
        current = getattr(current, 'parent', None)
    return '/'.join(reversed(parts))


def extract_clusters(clusters, connection_id, captured_at):
    """Keep source rule semantics; never pretend host rules are converted."""
    inventory = empty_inventory()
    known_vms, warnings = set(), []
    vm_id = lambda vm: f'{connection_id}:{moid(vm)}'
    for cluster in sorted(clusters, key=moid):
        for vm in sorted(cluster.vm or [], key=moid):
            identifier = vm_id(vm)
            if identifier not in known_vms:
                known_vms.add(identifier)
                config = getattr(vm, 'config', None)
                inventory['vms'].append(dict(id=identifier, name=vm.name,
                    os=getattr(config, 'guestFullName', None) or '', folder=folder_path(vm),
                    namespace='', target_name='', mode='cold', group='', dependencies=[]))
        config = cluster.configurationEx
        groups = {g.name: g for g in (config.group or [])}
        for rule in sorted(config.rule or [], key=lambda r: str(r.key)):
            raw_type = type(rule).__name__.split('.')[-1]
            rule_type = raw_type.removeprefix('Cluster')
            raw = dict(api_type=raw_type, cluster_id=moid(cluster), cluster_name=cluster.name,
                       connection_id=connection_id, captured_at=captured_at,
                       key=rule.key, uuid=getattr(rule, 'ruleUuid', None), name=rule.name,
                       enabled=rule.enabled, mandatory=getattr(rule, 'mandatory', None),
                       in_compliance=getattr(rule, 'inCompliance', None),
                       status=str(rule.status) if getattr(rule, 'status', None) is not None else None,
                       user_created=getattr(rule, 'userCreated', None))
            members, missing = [], []
            if rule_type in ('AffinityRuleSpec', 'AntiAffinityRuleSpec'):
                members = sorted(vm_id(v) for v in (rule.vm or []))
                raw['kind'] = 'vm-affinity' if rule_type == 'AffinityRuleSpec' else 'vm-anti-affinity'
                raw['vms'] = members
            elif rule_type == 'VmHostRuleInfo':
                raw['kind'] = 'vm-host'
                group_name = getattr(rule, 'vmGroupName', None)
                vm_group = groups.get(group_name)
                if vm_group is None or not hasattr(vm_group, 'vm'):
                    missing.append(f'VMグループ {group_name}')
                else:
                    members = sorted(vm_id(v) for v in (vm_group.vm or []))
                raw['vm_group'] = dict(name=group_name, members=members)
                for field, prop in (('affine_hosts', 'affineHostGroupName'), ('anti_affine_hosts', 'antiAffineHostGroupName')):
                    name = getattr(rule, prop, None)
                    group = groups.get(name)
                    hosts = None
                    if name:
                        if group is None or not hasattr(group, 'host'):
                            missing.append(f'ホストグループ {name}')
                        else:
                            hosts = sorted((dict(id=f'{connection_id}:{moid(h)}', name=h.name) for h in (group.host or [])), key=lambda h: h['id'])
                    raw[field] = dict(name=name, members=hosts)
            else:
                raw['kind'] = 'unsupported'
                members = sorted(vm_id(v) for v in (getattr(rule, 'vm', None) or []))
                missing.append(f'未対応のルール型 {raw_type}（共通属性のみ保存）')
            unresolved = sorted(set(members) - known_vms)
            raw['unresolved_vms'] = unresolved
            raw['unresolved_groups'] = missing
            identifier = f'{connection_id}:{moid(cluster)}:rule:{rule.key}'
            if missing or unresolved:
                warnings.append(dict(id=identifier, message='; '.join(missing + [f'未取得VM {v}' for v in unresolved])))
            inventory['placements'].append(dict(id=identifier, vms=members,
                source_rule=json.dumps(raw, ensure_ascii=False, sort_keys=True), target_rule='', verified=False))
    return dict(inventory=validate_inventory(inventory), warnings=warnings,
                captured_at=captured_at, connection_id=connection_id)


def discover(host, user, password, ca_file=None, port=443, cluster_ids=None):
    try:
        from pyVim.connect import SmartConnect, Disconnect
        from pyVmomi import vim
    except ImportError:
        raise RuntimeError('pyVmomiが必要です。python -m pip install -r requirements-vsphere.txt を実行してください。') from None
    context = ssl.create_default_context(cafile=ca_file)
    service = SmartConnect(host=host, user=user, pwd=password, port=port,
                           sslContext=context, httpConnectionTimeout=30)
    try:
        content = service.RetrieveContent()
        connection_id = content.about.instanceUuid
        if not connection_id:
            raise ValueError('vCenterのinstanceUuidを取得できません。')
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim.ClusterComputeResource], True)
        try:
            clusters = list(view.view)
            if not clusters:
                raise ValueError('参照可能なクラスタがありません。接続先と権限を確認してください。')
            if cluster_ids:
                requested = set(cluster_ids)
                clusters = [c for c in clusters if moid(c) in requested]
                if {moid(c) for c in clusters} != requested:
                    raise ValueError('指定されたクラスタを取得できません。IDと参照権限を確認してください。')
            return extract_clusters(clusters, connection_id, datetime.now(timezone.utc).isoformat())
        finally:
            view.Destroy()
    finally:
        Disconnect(service)


def main():
    parser = argparse.ArgumentParser(description='vSphereのVM・Affinity/Anti-Affinityを読取り専用で収集')
    parser.add_argument('--host', required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--port', type=int, default=443)
    parser.add_argument('--ca', help='vCenter証明書を検証するCAファイル')
    parser.add_argument('--cluster', action='append', help='クラスタのManaged Object ID。複数指定可能')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('portは1〜65535で指定してください。')
    if args.output.exists():
        parser.error('出力先が既に存在します。新しいファイル名を指定してください。')
    password = os.environ.get('VMW2RHOS_VCENTER_PASSWORD') or getpass('vCenter password: ')
    try:
        result = discover(args.host, args.user, password, args.ca, args.port, args.cluster)
        # Exclusive creation protects a file created while discovery was running.
        with args.output.open('x', encoding='utf-8') as output:
            json.dump(result['inventory'], output, ensure_ascii=False, indent=2)
        print(f"収集完了: VM {len(result['inventory']['vms'])}台 / 配置ルール {len(result['inventory']['placements'])}件")
        for warning in result['warnings']:
            print(f"要確認: {warning['id']}: {warning['message']}", file=sys.stderr)
    except (RuntimeError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    except Exception as error:
        # SDK exception text can contain endpoint details; do not log credentials.
        parser.exit(1, f'収集失敗 ({type(error).__name__})。接続、証明書、権限と出力先を確認してください。\n')


if __name__ == '__main__':
    main()

