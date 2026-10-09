"""Compare source, resolved intent and separately supplied observations."""

from collections import Counter
from datetime import datetime
import hashlib
import json

from .planner import build_plan

COMPARE_FIELDS = {
    'vms': {'name': str, 'namespace': str, 'os': str},
    'volumes': {'capacity_gib': int, 'destination': str, 'access': str, 'vms': list},
    'nics': {'network': str, 'vlan': int, 'vm': str},
    'placements': {'rule': (str, dict), 'vms': list},
}


def fingerprint(inventory):
    return hashlib.sha256(json.dumps(inventory, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def structured_rule(value):
    if not value:
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else value
    except ValueError:
        return value


def resources(inventory, target=False):
    result = []
    upgraded = {a['vm'] for a in inventory['actions'] if a['kind'] == 'os-upgrade'}
    for vm in inventory['vms']:
        result.append(dict(table='vms', id=vm['id'], values={
            'name': vm['target_name'] if target else vm['name'],
            'namespace': vm['namespace'] if target else vm['folder'],
            'os': None if target and vm['id'] in upgraded else vm['os'] or None}))
    for volume in inventory['volumes']:
        destination = (volume['storage_class'] if volume['strategy'] == 'copy' else volume['endpoint']) if target else volume['source']
        result.append(dict(table='volumes', id=volume['id'], values={
            'capacity_gib': volume['target_gib'] if target and volume['strategy'] == 'copy' else volume['capacity_gib'],
            'destination': destination or None, 'access': volume['access'], 'vms': sorted(volume['vms'])}))
    for nic in inventory['nics']:
        result.append(dict(table='nics', id=nic['id'], values={
            'network': (nic['target_network'] if target else nic['source']) or None,
            'vlan': nic['target_vlan'] if target else nic['source_vlan'], 'vm': nic['vm']}))
    for rule in inventory['placements']:
        result.append(dict(table='placements', id=rule['id'], values={
            'rule': structured_rule(rule['target_rule'] if target else rule['source_rule']),
            'vms': sorted(rule['vms'])}))
    return result


def validate_observation(data):
    keys = {'schema_version', 'captured_at', 'plan_fingerprint', 'resources'}
    if not isinstance(data, dict) or set(data) != keys or type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('移行先実測JSONの形式が不正です。テンプレートを使用してください。')
    if not isinstance(data['captured_at'], str):
        raise ValueError('captured_atにタイムゾーン付きの取得日時が必要です。')
    try:
        date = datetime.fromisoformat(data['captured_at'].replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise ValueError('captured_atにタイムゾーン付きの取得日時が必要です。') from None
    if not isinstance(data['plan_fingerprint'], str) or not isinstance(data['resources'], list):
        raise ValueError('計画識別子と実測リソースの配列が必要です。')
    seen = set()
    for resource in data['resources']:
        if not isinstance(resource, dict) or set(resource) != {'table', 'id', 'values'}:
            raise ValueError('実測リソースの項目が不正です。')
        table, identifier, values = resource['table'], resource['id'], resource['values']
        if not isinstance(table, str) or table not in COMPARE_FIELDS or not isinstance(identifier, str) or not identifier:
            raise ValueError('比較対象の種類と元の安定したIDを指定してください。')
        key = (table, identifier)
        if key in seen:
            raise ValueError('実測リソースのIDが重複しています。')
        seen.add(key)
        if not isinstance(values, dict) or not set(values) <= set(COMPARE_FIELDS[table]):
            raise ValueError('実測値に未知の比較項目があります。')
        for field, value in values.items():
            if value is None:
                continue
            expected = COMPARE_FIELDS[table][field]
            allowed = expected if isinstance(expected, tuple) else (expected,)
            if type(value) not in allowed:
                raise ValueError(f'{table}.{field}: 実測値の型が不正です。')
            if isinstance(value, list) and (any(type(x) is not str for x in value) or len(set(value)) != len(value)):
                raise ValueError('関連VMは重複のないID配列で指定してください。')
            if isinstance(value, int) and (value < 0 or (field == 'vlan' and value > 4094)):
                raise ValueError('容量またはVLANの実測値が範囲外です。')
    return data


def observation_template(inventory):
    plan = build_plan(inventory)
    return dict(schema_version=1, captured_at='', plan_fingerprint=fingerprint(inventory),
                resources=[dict(table=r['table'], id=r['id'],
                                values={key: None for key in r['values']})
                           for r in resources(plan['inventory'], target=True)])


def compare(inventory, observation=None):
    plan = build_plan(inventory)
    source = {(r['table'], r['id']): r['values'] for r in resources(inventory)}
    expected = {(r['table'], r['id']): r['values'] for r in resources(plan['inventory'], True)}
    observed, stale = {}, False
    if observation is not None:
        validate_observation(observation)
        stale = observation['plan_fingerprint'] != fingerprint(inventory)
        observed = {(r['table'], r['id']): r['values'] for r in observation['resources']}
    rows = []
    for (table, identifier), fields in expected.items():
        actual = observed.get((table, identifier), {})
        for field, intended in fields.items():
            original = source[(table, identifier)][field]
            value = actual.get(field)
            # Membership order is not a configuration difference.
            if field == 'vms' and value is not None:
                value = sorted(value)
            change = original is not None and intended is not None and original != intended
            if intended in (None, ''):
                status = 'unresolved'
            elif observation is None:
                status = 'planned-change' if change else 'unchanged'
            elif stale:
                status = 'stale-observation'
            elif (table, identifier) not in observed:
                status = 'missing-resource'
            elif value is None:
                status = 'unobserved'
            elif value != intended:
                status = 'mismatch'
            else:
                status = 'expected-change' if change else 'match'
            rows.append(dict(table=table, id=identifier, field=field, source=original,
                             expected=intended, observed=value, intentional_change=change, status=status))
    for (table, identifier), values in observed.items():
        if (table, identifier) not in expected:
            rows.append(dict(table=table, id=identifier, field='resource', source=None,
                             expected=None, observed=values, intentional_change=False,
                             status='stale-observation' if stale else 'unexpected-resource'))
    return dict(schema_version=1, plan_fingerprint=fingerprint(inventory), rows=rows,
                counts=dict(Counter(r['status'] for r in rows)), issues=plan['issues'],
                observation_stale=stale,
                captured_at=observation['captured_at'] if observation else None,
                observed_fields_match=bool(rows) and observation is not None and not stale
                and all(r['status'] in ('match', 'expected-change') for r in rows),
                migration_verified=False)

