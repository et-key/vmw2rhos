from copy import deepcopy
import json
from pathlib import Path
import unittest

from vmw2rhos.planner import build_plan, validate_inventory

SAMPLE = Path(__file__).parents[1] / 'vmw2rhos/web/sample.json'


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(SAMPLE.read_text(encoding='utf-8'))

    def codes(self):
        return {i['code'] for i in build_plan(self.data)['issues']}

    def test_sample_reports_real_unknowns(self):
        plan = build_plan(self.data)
        self.assertFalse(plan['locally_valid'])
        self.assertFalse(plan['executable'])
        self.assertIn('unverified-network', self.codes())
        self.assertIn('required', self.codes())

    def test_resolution_keeps_source_and_inventory_untouched(self):
        before = deepcopy(self.data)
        plan = build_plan(self.data)
        self.assertEqual(plan['inventory']['vms'][0]['namespace'], 'production-vms')
        self.assertEqual(plan['inventory']['volumes'][0]['source'], 'datastore01')
        self.assertEqual(plan['provenance'][0]['origin'], 'ns-production')
        self.assertEqual(self.data, before)

    def test_individual_override_wins(self):
        self.data['vms'][0]['namespace'] = 'custom'
        self.data['rules'].append(dict(id='conflict', kind='namespace', source='production', target='other'))
        plan = build_plan(self.data)
        self.assertEqual(plan['inventory']['vms'][0]['namespace'], 'custom')
        self.assertTrue(any(i['id'] == 'vc01:vm-102' and i['code'] == 'rule-conflict' for i in plan['issues']))
        self.assertFalse(any(i['id'] == 'vc01:vm-101' and i['code'] == 'rule-conflict' for i in plan['issues']))

    def test_dependency_order_and_cycle(self):
        self.assertEqual(build_plan(self.data)['order'], ['vc01:vm-101', 'vc01:vm-102'])
        self.data['vms'][0]['dependencies'] = ['vc01:vm-102']
        self.assertIn('dependency-cycle', self.codes())

    def test_missing_reference(self):
        self.data['nics'][0]['vm'] = 'missing'
        self.assertIn('missing-reference', self.codes())

    def test_capacity_shortfall(self):
        self.data['volumes'][0]['target_gib'] = 20
        self.assertIn('capacity', self.codes())

    def test_shared_copy_cannot_cross_namespaces(self):
        volume = self.data['volumes'][2]
        volume.update(kind='shared', strategy='copy', storage_class='shared')
        self.data['vms'][0]['namespace'] = 'other'
        self.assertIn('shared-namespace', self.codes())

    def test_shared_cutover_group_required(self):
        self.data['vms'][0]['group'] = ''
        self.assertIn('shared-group', self.codes())

    def test_external_copy_is_not_generic_disk_copy(self):
        self.data['volumes'][2].update(strategy='copy', storage_class='shared')
        self.assertIn('unsupported-copy', self.codes())

    def test_unknown_vlan_is_not_untagged(self):
        self.data['nics'][0].update(target_vlan=None, verified=True)
        self.assertIn('unverified-network', self.codes())
        self.data['nics'][0]['target_vlan'] = 0
        plan = build_plan(self.data)
        self.assertFalse(any(i['id'] == 'nic-storage' and i['code'] == 'unverified-network' for i in plan['issues']))

    def test_invalid_vlan_and_names(self):
        self.data['nics'][0]['target_vlan'] = 4095
        self.data['vms'][0]['namespace'] = 'Invalid Namespace'
        self.assertIn('vlan-range', self.codes())
        self.assertIn('invalid-name', self.codes())

    def test_duplicate_destination(self):
        self.data['vms'][1]['target_name'] = 'storage01'
        self.assertIn('duplicate-target', self.codes())

    def test_schema_rejects_ambiguous_inputs(self):
        for change in ('duplicate', 'bool-number', 'string-bool', 'unknown-field', 'enum', 'duplicate-ref'):
            with self.subTest(change=change):
                data = deepcopy(self.data)
                if change == 'duplicate':
                    data['vms'].append(deepcopy(data['vms'][0]))
                elif change == 'bool-number':
                    data['volumes'][0]['capacity_gib'] = True
                elif change == 'string-bool':
                    data['volumes'][0]['verified'] = 'true'
                elif change == 'unknown-field':
                    data['password'] = 'never persist'
                elif change == 'enum':
                    data['vms'][0]['mode'] = 'auto'
                else:
                    data['volumes'][0]['vms'] *= 2
                with self.assertRaises(ValueError):
                    validate_inventory(data)

    def test_local_success_never_means_live_execution_ready(self):
        for table in ('volumes', 'nics', 'placements', 'actions'):
            for row in self.data[table]:
                row['verified'] = True
        self.data['actions'][0]['definition'] = 'upgrade-windows-reviewed-v1'
        plan = build_plan(self.data)
        self.assertEqual(plan['issues'], [])
        self.assertTrue(plan['locally_valid'])
        self.assertFalse(plan['executable'])
        self.assertTrue(plan['live_checks'])


if __name__ == '__main__':
    unittest.main()
