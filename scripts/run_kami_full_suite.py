"""Current fixture suite: deny external HTTP/DB; permit asyncio loopback IPC."""
import socket
import argparse
from unittest.mock import patch
import pytest
from scripts.run2_kami_offline import network_guard


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--junitxml',default='reports/KAMI_INTEGRATION_2026-10-05/P2_ADVISORY_SELECTION_FIXED_2026-10-06/FULL_CURRENT_IPC.xml')
    args=parser.parse_args()
    original_connect=socket.socket.connect;original_connect_ex=socket.socket.connect_ex
    def ipc(address):
        return (isinstance(address,tuple) and address[0] in {'127.0.0.1','::1'}
                and address[1] not in {5432,55447,55448,55449,55450,55451})
    with network_guard(database_allowed=False):
        guarded_connect=socket.socket.connect;guarded_connect_ex=socket.socket.connect_ex
        def connect(sock,address):
            return original_connect(sock,address) if ipc(address) else guarded_connect(sock,address)
        def connect_ex(sock,address):
            return original_connect_ex(sock,address) if ipc(address) else guarded_connect_ex(sock,address)
        with patch.object(socket.socket,'connect',connect),patch.object(socket.socket,'connect_ex',connect_ex):
            return pytest.main(['tests','-q','-p','no:cacheprovider',
                '--junitxml='+args.junitxml])


if __name__=='__main__':raise SystemExit(main())
