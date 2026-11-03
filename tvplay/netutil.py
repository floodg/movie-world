"""Local-network address checks."""
import ipaddress
import socket
from urllib.parse import urlparse


def is_lan_address(host, allow_loopback=False):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_loopback:
        return allow_loopback
    if address.is_multicast or address.is_unspecified:
        return False
    return address.is_private or address.is_link_local


def lan_url_host(url):
    """Return the host of an http URL if it is a literal private-network address, else None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme != "http" or not parsed.hostname:
        return None
    if not is_lan_address(parsed.hostname):
        return None
    return parsed.hostname


def local_ip_for(remote_host):
    """The local IPv4 address the OS would use to reach remote_host."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((remote_host, 9))
        return sock.getsockname()[0]
    finally:
        sock.close()
