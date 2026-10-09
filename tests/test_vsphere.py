import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch, Mock

from vmw2rhos.collect_vsphere import extract_clusters, discover


def typed(type_name, **values):
    return type(type_name, (), values)()


def fixture():
    vm1 = NS(_moId='vm-1', name='app', config=NS(guestFullName='Linux'), parent=None)
    vm2 = NS(_moId='vm-2', name='db', config=NS(guestFullName='Windows'), parent=None)
    host = NS(_moId='host-1', name='esx01')
    rules = [
        typed('AffinityRuleSpec', key=1, name='together', enabled=False, mandatory=True, vm=[vm1, vm2]),
        typed('AntiAffinityRuleSpec', key=2, name='apart', enabled=True, mandatory=False, vm=[vm1, vm2]),
        typed('VmHostRuleInfo', key=3, name='on-hosts', enabled=True, mandatory=True,
              vmGroupName='apps', affineHostGroupName='hosts', antiAffineHostGroupName=None),
    ]
    groups = [NS(name='apps', vm=[vm1]), NS(name='hosts', host=[host])]
    return NS(_moId='domain-c1', name='cluster', vm=[vm1, vm2], configurationEx=NS(rule=rules, group=groups))


class DiscoveryTests(unittest.TestCase):
    def test_affinity_anti_affinity_disabled_and_strength_preserved(self):
        inventory = extract_clusters([fixture()], 'vc-uuid', '2026-10-09T14:00:00Z')['inventory']
        raw = [json.loads(r['source_rule']) for r in inventory['placements']]
        self.assertEqual(raw[0]['kind'], 'vm-affinity')
        self.assertFalse(raw[0]['enabled'])
        self.assertTrue(raw[0]['mandatory'])
        self.assertEqual(raw[1]['kind'], 'vm-anti-affinity')
        self.assertFalse(raw[1]['mandatory'])
        self.assertTrue(all(r['target_rule'] == '' and not r['verified'] for r in inventory['placements']))

    def test_vm_host_groups_and_stable_ids_preserved(self):
        result = extract_clusters([fixture()], 'vc-uuid', '2026-10-09T14:00:00Z')
        placement = result['inventory']['placements'][2]
        raw = json.loads(placement['source_rule'])
        self.assertEqual(placement['vms'], ['vc-uuid:vm-1'])
        self.assertEqual(raw['affine_hosts']['members'], [{'id': 'vc-uuid:host-1', 'name': 'esx01'}])
        self.assertEqual(placement['id'], 'vc-uuid:domain-c1:rule:3')
        self.assertEqual(result['warnings'], [])

    def test_missing_group_is_not_silently_dropped(self):
        cluster = fixture()
        cluster.configurationEx.group = []
        result = extract_clusters([cluster], 'vc-uuid', '2026-10-09T14:00:00Z')
        self.assertEqual(len(result['inventory']['placements']), 3)
        self.assertTrue(result['warnings'])
        self.assertTrue(json.loads(result['inventory']['placements'][2]['source_rule'])['unresolved_groups'])

    def test_missing_vm_and_unknown_type_remain_visible(self):
        cluster = fixture()
        cluster.configurationEx.rule.append(typed('UnknownRule', key=4, name='unknown', enabled=True, vm=[]))
        cluster.vm = cluster.vm[:1]
        result = extract_clusters([cluster], 'vc-uuid', '2026-10-09T14:00:00Z')
        self.assertEqual(len(result['inventory']['placements']), 4)
        self.assertEqual(json.loads(result['inventory']['placements'][0]['source_rule'])['unresolved_vms'], ['vc-uuid:vm-2'])
        self.assertEqual(json.loads(result['inventory']['placements'][-1]['source_rule'])['kind'], 'unsupported')

    def test_connection_cleanup_and_tls_verification(self):
        cluster = fixture()
        view = NS(view=[cluster], Destroy=Mock())
        content = NS(about=NS(instanceUuid='vc-uuid'), rootFolder='root', viewManager=NS(CreateContainerView=Mock(return_value=view)))
        service = NS(RetrieveContent=Mock(return_value=content))
        connect = NS(SmartConnect=Mock(return_value=service), Disconnect=Mock())
        sdk = NS(vim=NS(ClusterComputeResource='cluster-type'))
        with patch.dict('sys.modules', {'pyVim': NS(connect=connect), 'pyVim.connect': connect, 'pyVmomi': sdk}):
            result = discover('vcenter.example', 'user', 'secret')
        self.assertEqual(result['connection_id'], 'vc-uuid')
        kwargs = connect.SmartConnect.call_args.kwargs
        self.assertTrue(kwargs['sslContext'].check_hostname)
        self.assertEqual(kwargs['httpConnectionTimeout'], 30)
        view.Destroy.assert_called_once()
        connect.Disconnect.assert_called_once_with(service)

    def test_cleanup_when_requested_cluster_is_missing(self):
        view = NS(view=[fixture()], Destroy=Mock())
        service = NS(RetrieveContent=Mock(return_value=NS(about=NS(instanceUuid='vc'), rootFolder='root', viewManager=NS(CreateContainerView=Mock(return_value=view)))))
        connect = NS(SmartConnect=Mock(return_value=service), Disconnect=Mock())
        with patch.dict('sys.modules', {'pyVim': NS(connect=connect), 'pyVim.connect': connect, 'pyVmomi': NS(vim=NS(ClusterComputeResource='cluster-type'))}):
            with self.assertRaises(ValueError):
                discover('vcenter.example', 'user', 'secret', cluster_ids=['missing'])
        view.Destroy.assert_called_once()
        connect.Disconnect.assert_called_once_with(service)


if __name__ == '__main__':
    unittest.main()
