#!/usr/bin/env python3
"""Authenticated HTTP/CONNECT gateway. Consent comes only from host stdin.

The agent never receives the control channel or the host's preference files.
Each approved DNS result is checked and the connection is pinned to that IP.
"""
import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import select
import socket
import sys
import threading
from urllib.parse import urlsplit
import uuid


def public_address(address):
    ip = ipaddress.ip_address(address.split('%', 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # Transition addresses can embed otherwise blocked IPv4 destinations.
    if isinstance(ip, ipaddress.IPv6Address) and (ip.sixtofour or ip.teredo):
        return False
    if isinstance(ip, ipaddress.IPv6Address) and any(ip in network for network in (
            ipaddress.ip_network('64:ff9b::/96'), ipaddress.ip_network('64:ff9b:1::/48'))):
        return False
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def connect_public(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not public_address(a[4][0]) for a in addresses):
        raise ValueError('Private, local, and reserved destinations are blocked.')
    for family, socktype, proto, _, address in addresses:
        connection = socket.socket(family, socktype, proto)
        connection.settimeout(15)
        try:
            connection.connect(address)
            return connection
        except OSError:
            connection.close()
    raise OSError('The public destination could not be reached.')


class Consent:
    def __init__(self, source=sys.stdin, destination=sys.stdout):
        self.source, self.destination = source, destination
        self.pending = {}
        self.lock = threading.Lock()
        self.closed = False

    def emit(self, message):
        self.destination.write(json.dumps(message) + '\n')
        self.destination.flush()

    def read_decisions(self):
        try:
            for line in self.source:
                try:
                    reply = json.loads(line)
                    with self.lock:
                        pending = self.pending.get(reply.get('id'))
                        if pending:
                            pending['allow'] = reply.get('allow') is True
                            pending['event'].set()
                except (ValueError, AttributeError):
                    continue
        finally:
            with self.lock:
                self.closed = True
                for pending in self.pending.values():
                    pending['event'].set()

    def request(self, host, port, protocol):
        key = uuid.uuid4().hex
        pending = {'event': threading.Event(), 'allow': False}
        with self.lock:
            if self.closed or len(self.pending) >= 16:
                return False
            self.pending[key] = pending
            try:
                self.emit({'event': 'request', 'id': key, 'host': host,
                           'port': port, 'protocol': protocol})
            except (OSError, ValueError):
                self.pending.pop(key, None)
                return False
        pending['event'].wait(120)
        with self.lock:
            self.pending.pop(key, None)
        return pending['allow']


class Proxy(BaseHTTPRequestHandler):
    # Avoid read-ahead: the raw socket relay must receive the entire HTTP body.
    rbufsize = 0
    protocol_version = 'HTTP/1.1'
    server_version = 'ollama-box-gateway'

    def log_message(self, *_):
        pass  # Never log URLs, authorization headers, or request bodies.

    def handle_one_request(self):
        self.connection.settimeout(130)
        super().handle_one_request()

    def destination(self):
        if self.command == 'CONNECT':
            parsed = urlsplit('//' + self.path)
            protocol, port = 'HTTPS', parsed.port or 443
        else:
            parsed = urlsplit(self.path)
            if parsed.scheme != 'http':
                raise ValueError('Use HTTP proxying or HTTPS CONNECT.')
            protocol, port = 'HTTP', parsed.port or 80
        host = parsed.hostname
        if (not host or parsed.username or parsed.password or len(host) > 253 or
                any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:' for c in host)):
            raise ValueError('Invalid destination.')
        if not 1 <= port <= 65535:
            raise ValueError('Invalid port.')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not public_address(host):
                raise ValueError('Private, local, and reserved destinations are blocked.')
        return host, port, protocol, parsed

    def forward(self):
        self.close_connection = True
        auth = self.headers.get('Proxy-Authorization', '')
        if not hmac.compare_digest(auth.encode('latin-1'), self.server.authorization.encode('ascii')):
            self.send_response(407, 'A valid session proxy credential is required.')
            self.send_header('Proxy-Authenticate', 'Basic realm="ollama-box"')
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close')
            self.end_headers()
            return
        try:
            host, port, protocol, parsed = self.destination()
        except ValueError as error:
            self.send_error(403, str(error))
            return
        if not self.server.consent.request(host, port, protocol):
            self.send_error(403, 'Internet access denied. Continue working offline.')
            return
        try:
            upstream = connect_public(host, port)
        except (OSError, ValueError):
            self.send_error(403, 'Destination blocked or unavailable. Continue working offline.')
            return
        with upstream:
            if self.command == 'CONNECT':
                self.send_response(200, 'Connection established')
                self.end_headers()
            else:
                path = parsed.path or '/'
                if parsed.query:
                    path += '?' + parsed.query
                # Rebuild authority from the validated target; never forward proxy credentials.
                authority = '[' + host + ']' if ':' in host else host
                headers = [f'{self.command} {path} HTTP/1.1', f'Host: {authority}:{port}',
                           'Connection: close']
                for name, value in self.headers.items():
                    if name.lower() not in {'host', 'connection', 'proxy-authorization', 'proxy-connection'}:
                        headers.append(f'{name}: {value}')
                upstream.sendall(('\r\n'.join(headers) + '\r\n\r\n').encode('latin-1'))
            try:
                self.relay(upstream)
            except OSError:
                pass  # A denied/closed connection must not take down other sessions.

    def relay(self, upstream):
        peers = [self.connection, upstream]
        while peers:
            readable, _, _ = select.select(peers, [], [], 300)
            if not readable:
                return
            for source in readable:
                data = source.recv(65536)
                destination = upstream if source is self.connection else self.connection
                if not data:
                    peers.remove(source)
                    destination.shutdown(socket.SHUT_WR)
                else:
                    destination.sendall(data)

    do_CONNECT = do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = forward


def main():
    token = os.environ['OLLAMA_PROXY_TOKEN']
    if len(token) < 32:
        raise ValueError('A unique session credential is required.')
    server = ThreadingHTTPServer(('0.0.0.0', 3128), Proxy)
    server.daemon_threads = True
    server.authorization = 'Basic ' + base64.b64encode(('box:' + token).encode()).decode()
    server.consent = Consent()
    def control():
        server.consent.read_decisions()
        server.shutdown()

    threading.Thread(target=control, daemon=True).start()
    server.consent.emit({'event': 'ready'})
    server.serve_forever()


if __name__ == '__main__':
    main()
