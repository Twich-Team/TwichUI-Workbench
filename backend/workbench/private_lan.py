"""Restrict the dedicated HTTPS service to the configured private IPv4 subnet."""
from ipaddress import IPv4Address, IPv4Network
import os

PRIVATE = tuple(IPv4Network(block) for block in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
PORTAL_AUTHORITY = 'workbench.twich.gg'


def is_portal(lan, host):
    """The owner-approved proxy has one actual peer and one HTTPS authority.

    This complements the fixed administrator /32 exception. Forwarded headers
    never establish the peer, and other private listener configurations retain
    their existing subnet boundary.
    """
    return (lan[0] == '10.0.0.9:9487' and lan[1] == IPv4Network('10.0.0.0/24')
            and host == '10.0.4.3')


def allows_peer(subnet, host):
    try:
        peer = IPv4Address(host)
    except ValueError:
        return False
    return peer in subnet and peer not in (subnet.network_address, subnet.broadcast_address)


def configured():
    listener = os.environ.get('WORKBENCH_PRIVATE_LAN_IP')
    network = os.environ.get('WORKBENCH_PRIVATE_LAN_SUBNET')
    if listener is None and network is None and 'WORKBENCH_PRIVATE_LAN_CLIENT' not in os.environ:
        return None
    server, subnet = IPv4Address(listener), IPv4Network(network)
    if not allows_peer(subnet, server) or not any(subnet != block and subnet.subnet_of(block) for block in PRIVATE):
        raise ValueError('Set a private IPv4 LAN subnet and a listener address within it.')
    return f'{server}:9487', subnet
