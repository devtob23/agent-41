"""Consent, gateway isolation, and terminal tests; no internet or Podman required."""
import base64
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'ollama-box' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


access = module('internet-access')
proxy = module('internet-proxy')


class PolicyTests(unittest.TestCase):
    def test_all_startup_choices_and_persistence_are_per_model(self):
        for choice, mode, saved in [('1', 'allow', 'ask'), ('2', 'allow', 'allow'),
                                    ('3', 'ask', 'ask'), ('4', 'deny', 'deny'), ('', 'ask', 'ask')]:
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as d:
                policy = access.Policy(d, '../model:tag')
                actual = access.initial_policy(policy, interactive=True,
                                               read=lambda _: choice, write=lambda _: None)
                self.assertEqual(actual, mode)
                self.assertEqual(policy.load(), saved)
                self.assertEqual(access.Policy(d, '../model:other-tag').load(), 'ask')
                if saved != 'ask':
                    self.assertEqual(policy.path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(policy.path.parent.parent, Path(d))

    def test_runtime_once_and_forever_and_reset(self):
        with tempfile.TemporaryDirectory() as d:
            policy = access.Policy(d, 'model:tag')
            self.assertEqual(access.connection_decision(policy, 'ask', '1'), (True, 'ask'))
            self.assertEqual(access.connection_decision(policy, 'ask', '3'), (False, 'ask'))
            self.assertEqual(policy.load(), 'ask')
            self.assertEqual(access.connection_decision(policy, 'ask', '2'), (True, 'allow'))
            self.assertEqual(policy.load(), 'allow')
            self.assertEqual(access.connection_decision(policy, 'ask', '4'), (False, 'deny'))
            self.assertEqual(policy.load(), 'deny')
            self.assertEqual(access.initial_policy(policy, 'reset', interactive=False), 'ask')
            self.assertEqual(policy.load(), 'ask')

    def test_noninteractive_and_corrupt_preferences_never_grant_access(self):
        with tempfile.TemporaryDirectory() as d:
            policy = access.Policy(d, 'model:tag')
            self.assertEqual(access.initial_policy(policy, interactive=False), 'ask')
            policy.path.parent.mkdir()
            policy.path.write_text('{broken')
            self.assertEqual(policy.load(), 'ask')
            policy.path.write_text(json.dumps({'model': 'different', 'policy': 'allow'}))
            self.assertEqual(policy.load(), 'ask')

    def test_proxy_flags_precede_image_and_keep_agent_arguments(self):
        command = ['podman', 'run', '--read-only', '--ollama-box-image', 'agent', '--model', 'local']
        actual = access.proxy_command(command, {'HTTPS_PROXY': 'http://gateway:3128'})
        self.assertEqual(actual, ['podman', 'run', '--read-only', '--env',
                                  'HTTPS_PROXY=http://gateway:3128', 'agent', '--model', 'local'])

    def test_failure_to_save_denial_does_not_abort_local_work(self):
        with tempfile.TemporaryDirectory() as d:
            policy = access.Policy(d, 'model:tag')
            with patch.object(policy, 'save', side_effect=OSError('read-only')), \
                    patch.object(sys, 'stderr', io.StringIO()):
                self.assertEqual(access.connection_decision(policy, 'ask', '4'), (False, 'deny'))
                self.assertEqual(access.initial_policy(policy, interactive=True, read=lambda _: '4',
                                                       write=lambda _: None), 'deny')

    def test_interrupted_preference_write_does_not_retain_permanent_allow(self):
        with tempfile.TemporaryDirectory() as d:
            policy = access.Policy(d, 'model:tag')
            policy.save('allow')
            with patch.object(access.os, 'write', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    policy.save('deny')
            self.assertEqual(policy.load(), 'ask')

    def test_denied_session_runs_agent_and_preserves_exit_status(self):
        with tempfile.TemporaryDirectory() as d:
            command = [sys.executable, str(ROOT / 'ollama-box/internet-access.py'),
                       '--config', d, '--model', 'model:tag', '--network', 'internal',
                       '--session', 'test', '--ollama-host', 'ollama', '--internet', 'deny', '--',
                       '--ollama-box-image', sys.executable, '-c', 'print("OFFLINE WORK CONTINUES"); exit(7)']
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertIn('OFFLINE WORK CONTINUES', result.stdout)


class ConsentTests(unittest.TestCase):
    def test_closed_control_channel_denies(self):
        consent = proxy.Consent(io.StringIO(''), io.StringIO())
        consent.read_decisions()
        self.assertFalse(consent.request('example.com', 443, 'HTTPS'))

    def test_only_host_control_channel_can_allow_a_request(self):
        read_fd, write_fd = os.pipe()
        source = os.fdopen(read_fd)
        destination = io.StringIO()
        consent = proxy.Consent(source, destination)
        reader = threading.Thread(target=consent.read_decisions)
        reader.start()
        result = []
        request = threading.Thread(target=lambda: result.append(consent.request('example.com', 443, 'HTTPS')))
        request.start()
        # Wait for the structured request, without an external network connection.
        for _ in range(100):
            with consent.lock:
                pending = list(consent.pending)
            if pending:
                break
            threading.Event().wait(.01)
        os.write(write_fd, (json.dumps({'id': pending[0], 'allow': True}) + '\n').encode())
        request.join(2)
        self.assertEqual(result, [True])
        os.close(write_fd)
        reader.join(2)
        source.close()


class GatewayLifecycleTests(unittest.TestCase):
    def test_failed_network_creation_never_removes_an_existing_resource(self):
        with tempfile.TemporaryDirectory() as d:
            gateway = access.Gateway(d, 'internal', 'test')
            calls = []
            def run(command, **_):
                calls.append(command)
                if command[:3] == ['podman', 'network', 'create']:
                    raise subprocess.CalledProcessError(125, command)
                return subprocess.CompletedProcess(command, 0)
            with patch.object(access.subprocess, 'run', side_effect=run):
                with self.assertRaises(subprocess.CalledProcessError):
                    gateway.start()
                gateway.close()
            self.assertFalse(any('rm' in command for command in calls))

    def test_gateway_is_cleaned_up_after_host_control_channel_closes(self):
        with tempfile.TemporaryDirectory() as d:
            gateway = access.Gateway(d, 'internal', 'test')
            calls = []
            original_popen = subprocess.Popen
            def run(command, **_):
                calls.append(command)
                code = 1 if command[:3] == ['podman', 'container', 'exists'] else 0
                return subprocess.CompletedProcess(command, code)
            def popen(command, **kwargs):
                calls.append(command)
                return original_popen([sys.executable, '-u', '-c',
                                       'import sys; print(\'{"event":"ready"}\'); sys.stdin.read()'], **kwargs)
            with patch.object(access.subprocess, 'run', side_effect=run), \
                    patch.object(access.subprocess, 'Popen', side_effect=popen):
                gateway.start()
                child = gateway.process
                gateway.close()
                gateway.close()  # Cleanup is idempotent.
            self.assertIsNotNone(child.poll())
            removals = [c for c in calls if 'rm' in c]
            self.assertEqual(removals, [['podman', 'rm', '-f', gateway.name],
                                        ['podman', 'network', 'rm', gateway.egress]])


class DestinationTests(unittest.TestCase):
    def test_private_and_translation_addresses_are_blocked(self):
        for address in ['127.0.0.1', '10.0.0.1', '192.168.1.1', '169.254.169.254',
                        '100.64.0.1', '::1', 'fe80::1', 'fd00::1', '::ffff:192.168.1.1',
                        '2002:c0a8:0101::', '64:ff9b::c0a8:101', '224.0.0.1']:
            with self.subTest(address=address):
                self.assertFalse(proxy.public_address(address))
        self.assertTrue(proxy.public_address('1.1.1.1'))
        self.assertTrue(proxy.public_address('2606:4700:4700::1111'))

    def test_dns_rebinding_and_mixed_public_private_answers_are_blocked(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 443)),
                     (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with patch.object(proxy.socket, 'getaddrinfo', return_value=addresses), \
                patch.object(proxy.socket, 'socket') as create:
            with self.assertRaises(ValueError):
                proxy.connect_public('example.com', 443)
            create.assert_not_called()

    def test_connection_uses_validated_address_without_second_dns_lookup(self):
        address = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 443))
        with patch.object(proxy.socket, 'getaddrinfo', return_value=[address]) as resolve, \
                patch.object(proxy.socket, 'socket') as create:
            proxy.connect_public('example.com', 443)
            resolve.assert_called_once()
            create.return_value.connect.assert_called_once_with(('1.1.1.1', 443))


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.server = proxy.ThreadingHTTPServer(('127.0.0.1', 0), proxy.Proxy)
        self.server.daemon_threads = True
        self.server.authorization = 'Basic ' + base64.b64encode(b'box:session-secret').decode()
        self.allowed = False
        self.requests = []
        class Gate:
            def request(_, *args):
                self.requests.append(args)
                return self.allowed
        self.server.consent = Gate()
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get(self, url, auth=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Proxy-Authorization': self.server.authorization} if auth else {}
        connection.request('GET', url, headers=headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_other_sessions_cannot_use_gateway_without_credential(self):
        self.assertEqual(self.get('http://example.com/', auth=False)[0], 407)
        self.assertEqual(self.requests, [])

    def test_denial_does_not_resolve_or_connect_and_next_request_still_works(self):
        with patch.object(proxy, 'connect_public') as connect:
            self.assertEqual(self.get('http://example.com/')[0], 403)
            self.assertEqual(self.get('http://example.com/next')[0], 403)
            connect.assert_not_called()
        self.assertEqual(len(self.requests), 2)

    def test_private_literal_is_blocked_even_if_allowed(self):
        self.allowed = True
        with patch.object(proxy, 'connect_public') as connect:
            self.assertEqual(self.get('http://127.0.0.1/')[0], 403)
            connect.assert_not_called()
        self.assertEqual(self.requests, [])

    def test_http_forwarding_strips_proxy_credential(self):
        self.allowed = True
        received = []
        client, upstream = socket.socketpair()
        def serve():
            data = b''
            with upstream:
                while b'\r\n\r\n' not in data:
                    data += upstream.recv(4096)
                received.append(data)
                upstream.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
        thread = threading.Thread(target=serve)
        thread.start()
        with patch.object(proxy, 'connect_public', return_value=client):
            self.assertEqual(self.get('http://example.com/path?q=value'), (200, b'OK'))
        thread.join(2)
        self.assertIn(b'GET /path?q=value HTTP/1.1', received[0])
        self.assertNotIn(b'session-secret', received[0])
        self.assertNotIn(b'Proxy-Authorization', received[0])

    def test_https_connect_relays_only_after_approval(self):
        self.allowed = True
        client, upstream = socket.socketpair()
        def echo():
            with upstream:
                data = upstream.recv(4096)
                upstream.sendall(data)
        thread = threading.Thread(target=echo)
        thread.start()
        connection = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3)
        with patch.object(proxy, 'connect_public', return_value=client):
            connection.sendall(('CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n'
                                'Proxy-Authorization: ' + self.server.authorization + '\r\n\r\n').encode())
            headers = b''
            while b'\r\n\r\n' not in headers:
                headers += connection.recv(4096)
            self.assertIn(b'200 Connection established', headers)
            connection.sendall(b'tunnel-data')
            self.assertEqual(connection.recv(4096), b'tunnel-data')
        connection.close()
        thread.join(2)
        self.assertEqual(self.requests, [('example.com', 443, 'HTTPS')])


class TerminalTests(unittest.TestCase):
    def test_runtime_denial_keeps_agent_running_and_consumes_only_approval_answer(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            result_path = t / 'decision.json'
            gateway_script = t / 'gateway.py'
            gateway_script.write_text('''import json,pathlib,sys,time
time.sleep(.2)
print(json.dumps({'event':'request','id':'one','host':'example.com','port':443,'protocol':'HTTPS'}),flush=True)
reply=sys.stdin.readline()
p=pathlib.Path(sys.argv[1]);temporary=p.with_suffix('.tmp');temporary.write_text(reply);temporary.replace(p)
time.sleep(10)
''')
            agent_script = t / 'agent.py'
            agent_script.write_text('''import json,pathlib,sys,time
p=pathlib.Path(sys.argv[1])
while not p.exists(): time.sleep(.01)
print('PERMISSION='+str(json.loads(p.read_text())['allow']),flush=True)
answer=input('LOCAL TASK: ')
print('CONTINUED: '+answer,flush=True)
''')
            driver = t / 'driver.py'
            driver.write_text('''import importlib.util,json,subprocess,sys
spec=importlib.util.spec_from_file_location('access',sys.argv[1])
access=importlib.util.module_from_spec(spec);spec.loader.exec_module(access)
class Gateway:
    def reply(self,request,allow):
        self.process.stdin.write((json.dumps({'id':request['id'],'allow':allow})+'\\n').encode())
        self.process.stdin.flush()
gateway=Gateway()
gateway.process=subprocess.Popen([sys.executable,sys.argv[2],sys.argv[4]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,bufsize=0)
try:
    status=access.supervise([sys.executable,sys.argv[3],sys.argv[4]],gateway,access.Policy(sys.argv[5],'model:tag'),'ask')
finally:
    gateway.process.terminate();gateway.process.communicate()
sys.exit(status)
''')
            master, slave = pty.openpty()
            command = [sys.executable, str(driver), str(ROOT / 'ollama-box/internet-access.py'),
                       str(gateway_script), str(agent_script), str(result_path), d]
            process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave)
            os.close(slave)
            transcript = bytearray()
            def until(marker):
                deadline = time.monotonic() + 8
                while marker not in transcript and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        try:
                            transcript.extend(os.read(master, 65536))
                        except OSError:
                            break
                self.assertIn(marker, transcript, transcript.decode(errors='replace'))
            try:
                until(b'Choose [1-4, default 3]:')
                os.write(master, b'3\n')
                until(b'LOCAL TASK:')
                self.assertIn(b'PERMISSION=False', transcript)
                os.write(master, b'local work\n')
                until(b'CONTINUED: local work')
                self.assertEqual(process.wait(timeout=3), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
                os.close(master)


if __name__ == '__main__':
    unittest.main()
