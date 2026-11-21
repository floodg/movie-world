"""Optional, private-LAN DNS-SD advertisement for the Android companion."""
import ipaddress
import socket
import threading

SERVICE_TYPE = "_movieworld._tcp.local."


def lan_addresses(host, adapters):
    """Only advertise interfaces the HTTP listener can actually serve."""
    addresses = set()
    for adapter in adapters:
        for interface in adapter.ips:
            value = interface.ip
            if not isinstance(value, str):
                continue
            try:
                address = ipaddress.IPv4Address(value)
            except ipaddress.AddressValueError:
                continue
            if (address.is_private and not address.is_loopback and not address.is_link_local
                    and not address.is_multicast and not address.is_unspecified
                    and (host == "0.0.0.0" or host == value)):
                addresses.add(value)
    return sorted(addresses)


class LanAdvertisement:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.closed = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="movie-world-discovery", daemon=True)
        self.thread.start()
        return self

    def _run(self):
        zc = None
        info = None
        try:
            import ifaddr
            from zeroconf import IPVersion, ServiceInfo, Zeroconf
            zc = Zeroconf(ip_version=IPVersion.V4Only)
            previous = None
            while not self.closed.is_set():
                addresses = lan_addresses(self.host, ifaddr.get_adapters())
                if addresses != previous:
                    if info is not None:
                        zc.unregister_service(info)
                        info = None
                    if previous is not None:
                        zc.close()
                        zc = Zeroconf(ip_version=IPVersion.V4Only)
                    if addresses:
                        hostname = socket.gethostname().split(".")[0]
                        info = ServiceInfo(SERVICE_TYPE, f"Movie World on {hostname}.{SERVICE_TYPE}",
                            port=self.port, parsed_addresses=addresses,
                            properties={"app": "movie-world", "version": "1", "scheme": "http"},
                            server=f"{hostname}.local.")
                        zc.register_service(info, allow_name_change=True)
                    previous = addresses
                self.closed.wait(30)
        except Exception as error:
            # Discovery is optional; a saved/manual address remains usable.
            print(f"LAN discovery unavailable: {error}", flush=True)
        finally:
            if zc is not None:
                try:
                    if info is not None:
                        zc.unregister_service(info)
                finally:
                    zc.close()

    def stop(self):
        self.closed.set()
        if self.thread is not None:
            self.thread.join(timeout=5)


def advertise(host, port, scheme="http"):
    if scheme != "http" or host in ("127.0.0.1", "localhost", "::1"):
        return None
    return LanAdvertisement(host, port).start()
