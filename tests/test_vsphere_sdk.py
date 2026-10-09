"""Optional integration test against real SDK data objects, no vCenter needed."""

import json
from types import SimpleNamespace as NS
import unittest

try:
    from pyVmomi import vim
except ImportError:
    vim = None

from vmw2rhos.collect_vsphere import extract_clusters


@unittest.skipIf(vim is None, 'Optional pyVmomi SDK is not installed')
class SDKTests(unittest.TestCase):
    def test_actual_rule_types_and_group_membership(self):
        vm1, vm2 = vim.VirtualMachine('vm-1'), vim.VirtualMachine('vm-2')
        host = vim.HostSystem('host-1', stub=NS(InvokeAccessor=lambda *args: 'esx01'))
        rules = [
            vim.cluster.AffinityRuleSpec(key=1, name='together', enabled=False, mandatory=True, vm=[vm1, vm2]),
            vim.cluster.AntiAffinityRuleSpec(key=2, name='apart', enabled=True, mandatory=False, vm=[vm1, vm2]),
            vim.cluster.VmHostRuleInfo(key=3, name='hosts', enabled=True, mandatory=True, vmGroupName='apps', affineHostGroupName='hosts'),
        ]
        config = vim.cluster.ConfigInfoEx(rule=rules, group=[
            vim.cluster.VmGroup(name='apps', vm=[vm1]),
            vim.cluster.HostGroup(name='hosts', host=[host]),
        ])
        vms = [NS(_moId='vm-1', name='app', config=None, parent=None), NS(_moId='vm-2', name='db', config=None, parent=None)]
        result = extract_clusters([NS(_moId='domain-c1', name='cluster', vm=vms, configurationEx=config)], 'vc-uuid', '2026-10-09T14:00:00Z')
        raw = [json.loads(r['source_rule']) for r in result['inventory']['placements']]
        self.assertEqual([r['kind'] for r in raw], ['vm-affinity', 'vm-anti-affinity', 'vm-host'])
        self.assertEqual(raw[2]['affine_hosts']['members'][0]['name'], 'esx01')
        self.assertEqual(result['warnings'], [])


if __name__ == '__main__':
    unittest.main()
