import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tvplay.discovery import LanAdvertisement, advertise, lan_addresses


class DiscoveryTests(unittest.TestCase):
    def test_only_reachable_private_ipv4_interfaces_are_advertised(self):
        adapters = [SimpleNamespace(ips=[SimpleNamespace(ip=ip) for ip in
                    ["127.0.0.1", "169.254.1.2", "192.168.1.20", "10.0.0.2", "8.8.8.8", ("::1", 0, 0)]])]
        self.assertEqual(lan_addresses("0.0.0.0", adapters), ["10.0.0.2", "192.168.1.20"])
        self.assertEqual(lan_addresses("192.168.1.20", adapters), ["192.168.1.20"])
        self.assertEqual(lan_addresses("127.0.0.1", adapters), [])

    def test_loopback_and_https_do_not_advertise(self):
        with patch("tvplay.discovery.LanAdvertisement") as cls:
            self.assertIsNone(advertise("127.0.0.1", 80))
            self.assertIsNone(advertise("0.0.0.0", 443, "https"))
            cls.assert_not_called()

    def test_service_uses_http_port_and_is_withdrawn_on_shutdown(self):
        advertisement = LanAdvertisement("0.0.0.0", 8765)
        fake_zc = Mock()
        fake_info = Mock()
        zc_module = SimpleNamespace(IPVersion=SimpleNamespace(V4Only=4),
            ServiceInfo=Mock(return_value=fake_info), Zeroconf=Mock(return_value=fake_zc))
        adapter = SimpleNamespace(ips=[SimpleNamespace(ip="192.168.1.20")])
        fake_ifaddr = SimpleNamespace(get_adapters=lambda: [adapter])
        advertisement.closed.wait = lambda _: advertisement.closed.set()
        with patch.dict(sys.modules, zeroconf=zc_module, ifaddr=fake_ifaddr):
            advertisement._run()
        self.assertEqual(zc_module.ServiceInfo.call_args.kwargs["port"], 8765)
        fake_zc.register_service.assert_called_once_with(fake_info, allow_name_change=True)
        fake_zc.unregister_service.assert_called_once_with(fake_info)
        fake_zc.close.assert_called_once()

    def test_discovery_failure_does_not_escape(self):
        with patch.dict(sys.modules, zeroconf=None):
            LanAdvertisement("0.0.0.0", 80)._run()

    def test_changed_address_rebinds_interfaces_and_withdraws_old_service(self):
        advertisement = LanAdvertisement("0.0.0.0", 80)
        clients = [Mock(), Mock()]
        infos = [Mock(), Mock()]
        zc_module = SimpleNamespace(IPVersion=SimpleNamespace(V4Only=4),
            ServiceInfo=Mock(side_effect=infos), Zeroconf=Mock(side_effect=clients))
        addresses = iter(["192.168.1.20", "192.168.1.21"])
        fake_ifaddr = SimpleNamespace(get_adapters=lambda: [SimpleNamespace(ips=[SimpleNamespace(ip=next(addresses))])])
        ticks = []
        def wait(_):
            ticks.append(1)
            if len(ticks) == 2:
                advertisement.closed.set()
        advertisement.closed.wait = wait
        with patch.dict(sys.modules, zeroconf=zc_module, ifaddr=fake_ifaddr):
            advertisement._run()
        for client, info in zip(clients, infos):
            client.unregister_service.assert_called_once_with(info)
            client.close.assert_called_once()
        self.assertEqual(zc_module.ServiceInfo.call_args_list[1].kwargs["parsed_addresses"], ["192.168.1.21"])

    def test_virtual_adapter_address_is_not_advertised_on_home_interface(self):
        advertisement = LanAdvertisement("0.0.0.0", 80)
        clients = [Mock(), Mock()]
        infos = [Mock(), Mock()]
        zc_module = SimpleNamespace(IPVersion=SimpleNamespace(V4Only=4),
            ServiceInfo=Mock(side_effect=infos), Zeroconf=Mock(side_effect=clients))
        adapters = [SimpleNamespace(ips=[SimpleNamespace(ip=ip)]) for ip in ["172.20.1.1", "192.168.1.20"]]
        fake_ifaddr = SimpleNamespace(get_adapters=lambda: adapters)
        advertisement.closed.wait = lambda _: advertisement.closed.set()
        with patch.dict(sys.modules, zeroconf=zc_module, ifaddr=fake_ifaddr):
            advertisement._run()
        for create, info in zip(zc_module.Zeroconf.call_args_list, zc_module.ServiceInfo.call_args_list):
            self.assertEqual(create.kwargs["interfaces"], info.kwargs["parsed_addresses"])
            self.assertEqual(len(info.kwargs["parsed_addresses"]), 1)


if __name__ == "__main__":
    unittest.main()
