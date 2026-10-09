from copy import deepcopy
import json
from pathlib import Path
import unittest

from vmw2rhos.comparison import compare, observation_template, resources, validate_observation
from vmw2rhos.planner import build_plan


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.inventory = json.loads((Path(__file__).parents[1] / 'vmw2rhos/web/sample.json').read_text(encoding='utf-8'))
        # Upgrade intent has no target OS version yet; remove it for exact-match fixtures.
        self.inventory['actions'] = []
        self.observed = observation_template(self.inventory)
        self.observed['captured_at'] = '2026-10-09T14:30:00+00:00'
        self.observed['resources'] = resources(build_plan(self.inventory)['inventory'], True)

    def test_three_way_diff_recognizes_planned_vlan_change(self):
        self.inventory['nics'][0]['target_vlan'] = 130
        self.observed = observation_template(self.inventory)
        self.observed['captured_at'] = '2026-10-09T14:30:00Z'
        self.observed['resources'] = resources(build_plan(self.inventory)['inventory'], True)
        report = compare(self.inventory, self.observed)
        row = next(r for r in report['rows'] if r['id'] == 'nic-storage' and r['field'] == 'vlan')
        self.assertEqual((row['source'], row['expected'], row['observed'], row['status']), (120, 130, 130, 'expected-change'))
        self.assertTrue(report['observed_fields_match'])
        self.assertFalse(report['migration_verified'])

    def test_no_observation_is_never_evidence_of_success(self):
        report = compare(self.inventory)
        self.assertFalse(report['observed_fields_match'])
        self.assertIn('planned-change', report['counts'])
        self.assertNotIn('match', report['counts'])

    def test_mismatch_missing_extra_and_unknown_are_distinct(self):
        self.observed['resources'][0]['values']['namespace'] = 'wrong'
        self.observed['resources'][1]['values']['os'] = None
        self.observed['resources'] = [r for r in self.observed['resources'] if r['id'] != 'nic-app']
        self.observed['resources'].append(dict(table='vms', id='unexpected', values={'name': 'extra'}))
        report = compare(self.inventory, self.observed)
        for state in ('mismatch', 'unobserved', 'missing-resource', 'unexpected-resource'):
            self.assertIn(state, report['counts'])
        self.assertFalse(report['observed_fields_match'])

    def test_old_plan_observation_cannot_pass(self):
        self.inventory['vms'][0]['mode'] = 'warm'
        report = compare(self.inventory, self.observed)
        self.assertTrue(report['observation_stale'])
        self.assertNotIn('match', report['counts'])

    def test_template_never_prefills_actual_values(self):
        template = observation_template(self.inventory)
        self.assertTrue(all(value is None for r in template['resources'] for value in r['values'].values()))
        self.assertEqual(template['captured_at'], '')

    def test_os_upgrade_without_target_version_is_unresolved(self):
        self.inventory['actions'] = [dict(id='upgrade', vm='vc01:vm-102', kind='os-upgrade', phase='before', definition='upgrade', success_condition='version', failure_policy='stop', verified=True)]
        report = compare(self.inventory)
        row = next(r for r in report['rows'] if r['id'] == 'vc01:vm-102' and r['field'] == 'os')
        self.assertEqual(row['status'], 'unresolved')

    def test_membership_order_is_not_mismatch(self):
        self.observed['resources'][-1]['values']['vms'].reverse()
        self.assertTrue(compare(self.inventory, self.observed)['observed_fields_match'])

    def test_structured_rule_key_order_is_not_mismatch(self):
        self.inventory['placements'][0]['target_rule'] = '{"required":true,"type":"anti"}'
        self.observed = observation_template(self.inventory)
        self.observed['captured_at'] = '2026-10-09T14:30:00Z'
        self.observed['resources'] = resources(build_plan(self.inventory)['inventory'], True)
        self.observed['resources'][-1]['values']['rule'] = {'type': 'anti', 'required': True}
        self.assertTrue(compare(self.inventory, self.observed)['observed_fields_match'])

    def test_observation_validation(self):
        for case in ('timestamp', 'duplicate', 'bool-vlan', 'unknown-field'):
            with self.subTest(case=case):
                data = deepcopy(self.observed)
                if case == 'timestamp':
                    data['captured_at'] = '2026-10-09T14:00:00'
                elif case == 'duplicate':
                    data['resources'].append(data['resources'][0])
                elif case == 'bool-vlan':
                    next(r for r in data['resources'] if r['table'] == 'nics')['values']['vlan'] = True
                else:
                    data['resources'][0]['values']['unknown'] = 'not compared'
                with self.assertRaises(ValueError):
                    validate_observation(data)


if __name__ == '__main__':
    unittest.main()
