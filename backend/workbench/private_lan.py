"""Exact LAN listener/client policy. TLS client authentication is owned by the LAN service."""
from ipaddress import IPv4Address, IPv4Network
import os

PRIVATE = tuple(IPv4Network(block) for block in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def configured():
    listener = os.environ.get('WORKBENCH_PRIVATE_LAN_IP')
    client = os.environ.get('WORKBENCH_PRIVATE_LAN_CLIENT')
    if listener is None and client is None:
        return None
    server, owner = IPv4Address(listener), IPv4Address(client)
    if server == owner or any(not any(ip in block for block in PRIVATE) for ip in (server, owner)):
        raise ValueError('Exact distinct private IPv4 listener and owner-client addresses are required.')
    return f'{server}:9487', str(owner)
