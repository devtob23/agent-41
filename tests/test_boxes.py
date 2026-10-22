"""Run with python3 -m unittest discover -s tests -v. No Podman daemon required."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import shlex
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('validator', ROOT / 'ollama-box/validate-resource.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def functions(box):
    source = (ROOT / f'{box}-box/{box}-box').read_text()
    return source[:source.index('# --- Platform:')]


def terminal(script, env, answers):
    master, slave = pty.openpty()
    proc = subprocess.Popen(['bash', '-c', script], stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    # Readline discards input typed before the prompt appears;
    # so each answer is sent only once the output has settled.
    pending = [line + '\n' for line in answers.removesuffix('\n').split('\n')] if answers else []
    output = b''
    deadline = time.monotonic() + 8
    quiet = time.monotonic()
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                try:
                    chunk = os.read(master, 8192)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
                quiet = time.monotonic()
            elif pending and time.monotonic() - quiet > .15:
                os.write(master, pending.pop(0).encode())
                quiet = time.monotonic()
            if proc.poll() is not None:
                break
        if proc.poll() is None:
            proc.wait(timeout=1)
        return proc.returncode, output.decode()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        os.close(master)


class SetupTests(unittest.TestCase):
    def test_podman_requires_supported_version_and_rootless_runtime(self):
        for box in ['claude', 'codex', 'ollama']:
            source = (ROOT / f'{box}-box/{box}-box').read_text()
            definitions = source[:source.index('# Network options for pasta.')]
            for version, rootless, uid, accepted in [
                    ('4.3.1', 'true', '1000', False),
                    ('3.4.7', 'true', '1000', False),
                    ('4.4.0', 'true', '1000', True),
                    ('5.2.1', 'true', '1000', True),
                    ('5.2.1', 'false', '1000', False),
                    ('5.2.1', '', '1000', False),
                    ('5.2.1', 'true', '0', False)]:
                with self.subTest(box=box, version=version, rootless=rootless, uid=uid):
                    env = dict(os.environ, TEST_VERSION=version, TEST_ROOTLESS=rootless, TEST_UID=uid)
                    script = definitions + '''
id() { echo "$TEST_UID"; }
podman() {
  case "$1" in
    --version) echo "podman version $TEST_VERSION" ;;
    info) if [ "$#" -gt 1 ]; then echo "$TEST_ROOTLESS"; fi ;;
    *) return 1 ;;
  esac
}
PLATFORM=linux
check_podman
'''
                    result = subprocess.run(['bash', '-c', script], env=env, capture_output=True)
                    self.assertEqual(result.returncode == 0, accepted, result.stderr)

    def test_ambiguous_project_mount_paths_are_rejected(self):
        for box in ['claude', 'codex', 'ollama']:
            for path, accepted in [('/projects/my project', True),
                                   ('/projects/project:ro', False),
                                   ('/projects/project,other', False)]:
                with self.subTest(box=box, path=path):
                    script = functions(box) + '\nvalidate_project_path ' + shlex.quote(path)
                    result = subprocess.run(['bash', '-c', script], capture_output=True)
                    self.assertEqual(result.returncode == 0, accepted, result.stderr)

    def test_version_and_help_without_podman(self):
        for box in ['claude', 'codex', 'ollama']:
            for option in ['--help', '--version']:
                result = subprocess.run(['bash', str(ROOT / f'{box}-box/{box}-box'), option], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                if option == '--version':
                    self.assertIn(b'0.3.0', result.stdout)

    def test_confirm_reject_edit_persist_and_export(self):
        for box in ['claude', 'codex', 'ollama']:
            with self.subTest(box=box), tempfile.TemporaryDirectory() as d:
                t = Path(d)
                first, second = t / 'first choice', t / 'second;literal'
                first.mkdir(); second.mkdir()
                env = dict(os.environ)
                env.pop(box.upper() + '_BOX_ROOT', None)
                env[box.upper() + '_BOX_DIR'] = str(t / 'conf')
                script = functions(box) + '\nconfigure_projects\nprintf "EXPORTED=%s\\n" "${' + box.upper() + '_BOX_ROOT}"'
                code, output = terminal(script, env, f'{first}\nN\n{second}\nY\n')
                self.assertEqual(code, 0, output)
                self.assertIn('EXPORTED=' + str(second), output)
                self.assertEqual((t / 'conf/projects-root').read_text(), str(second) + '\n')
                again = subprocess.run(['bash', '-c', script], env=env, stdin=subprocess.DEVNULL, capture_output=True)
                self.assertEqual(again.returncode, 0, again.stderr)
                self.assertIn(str(second).encode(), again.stdout)
                env[box.upper() + '_BOX_ROOT'] = str(first)
                override = subprocess.run(['bash', '-c', script], env=env, stdin=subprocess.DEVNULL, capture_output=True)
                self.assertIn(str(first).encode(), override.stdout)

    def test_missing_noninteractive_path_fails(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, CODEX_BOX_DIR=d)
            env.pop('CODEX_BOX_ROOT', None)
            result = subprocess.run(['bash', '-c', functions('codex') + '\nconfigure_projects'], env=env,
                                    stdin=subprocess.DEVNULL, capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_network_default_and_explicit_shared(self):
        source = (ROOT / 'ollama-box/ollama-box').read_text()
        network = source[source.index('configure_network() {'):source.index('validate_resource() {')]
        for answer, mode in [('\n', 'isolated'), ('N\n', 'isolated'), ('Y\n', 'shared')]:
            with tempfile.TemporaryDirectory() as d:
                env = dict(os.environ, OLLAMA_BOX_DIR=d)
                env.pop('OLLAMA_BOX_NETWORK_MODE', None)
                script = functions('ollama') + network + '\nconfigure_network\necho MODE=$NETWORK_MODE'
                code, output = terminal(script, env, answer)
                self.assertEqual(code, 0, output)
                self.assertIn('MODE=' + mode, output)
                self.assertEqual(Path(d, 'network-mode').read_text(), mode + '\n')

    def test_model_source_default_confirm_edit_and_saved_choice(self):
        source = (ROOT / 'ollama-box/ollama-box').read_text()
        models = source[source.index('configure_models() {'):source.index('configure_network() {')]
        for answer in ['N', '']:
            with tempfile.TemporaryDirectory() as d:
                env = dict(os.environ, OLLAMA_BOX_DIR=d)
                env.pop('OLLAMA_BOX_MODELS_DIR', None)
                script = functions('ollama') + models + '\nconfigure_models\necho SOURCE=$MODELS_MOUNT'
                code, output = terminal(script, env, answer + '\n')
                self.assertEqual(code, 0, output)
                self.assertIn('SOURCE=ollama-box-models:/models-store:ro', output)
                self.assertEqual(Path(d, 'models-source').read_text(), 'managed\n')
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            first, second = t / 'first models', t / 'second models'
            for directory in [first, second]:
                (directory / 'blobs').mkdir(parents=True)
                (directory / 'manifests').mkdir()
            env = dict(os.environ, OLLAMA_BOX_DIR=str(t / 'conf'))
            env.pop('OLLAMA_BOX_MODELS_DIR', None)
            script = functions('ollama') + models + '\nconfigure_models\necho SOURCE=$MODELS_MOUNT'
            code, output = terminal(script, env, f'Y\n{t / "missing"}\n{first}\nN\n{second}\nY\n')
            self.assertEqual(code, 0, output)
            self.assertIn('SOURCE=' + str(second) + ':/models-store:ro', output)
            self.assertEqual((t / 'conf/models-source').read_text(), str(second) + '\n')
            again = subprocess.run(['bash', '-c', script], env=env, stdin=subprocess.DEVNULL, capture_output=True)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn(str(second).encode(), again.stdout)
            env['OLLAMA_BOX_MODELS_DIR'] = 'managed'
            managed = subprocess.run(['bash', '-c', script], env=env, stdin=subprocess.DEVNULL, capture_output=True)
            self.assertIn(b'ollama-box-models:/models-store:ro', managed.stdout)

    def test_external_models_reject_download_before_podman(self):
        source = (ROOT / 'ollama-box/ollama-box').read_text()
        definitions = source[:source.index('# --- ollama-box options')]
        result = subprocess.run(['bash', '-c', definitions + '\nMODELS_DIR=/existing-models\npull_model test-model'],
                                stdin=subprocess.DEVNULL, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('read-only'.encode(), result.stderr)


class ResourceTests(unittest.TestCase):
    scope = 'ollama-box-net-test'
    image = 'docker.io/ollama/ollama:latest'
    config = 'ctx=32768,gpu=cpu'

    def labels(self):
        return {'io.agent-41.owner': 'ollama-box', 'io.agent-41.scope': self.scope,
                'ollama-box.config': self.config}

    def container(self):
        return {'Config': {'Labels': self.labels(), 'Image': self.image, 'Entrypoint': ['/bin/ollama'],
                           'Cmd': ['serve'], 'Env': ['OLLAMA_CONTEXT_LENGTH=32768', 'OLLAMA_MODELS=/models-store/models']},
                'HostConfig': {'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                               'SecurityOpt': ['no-new-privileges']},
                'NetworkSettings': {'Networks': {self.scope: {}}},
                'Mounts': [{'Type': 'volume', 'Name': 'ollama-box-models', 'Destination': '/models-store', 'RW': False}]}

    def test_safe_resources(self):
        validator.validate('network', {'labels': self.labels(), 'internal': True}, self.scope)
        validator.validate('volume', {'Labels': self.labels(), 'Driver': 'local', 'Options': {}}, self.scope)
        validator.validate('container', self.container(), self.scope, self.image, self.config)

    def test_expanded_capability_drop_with_empty_bounding_set(self):
        item = self.container()
        item['HostConfig']['CapDrop'] = ['CAP_CHOWN', 'CAP_NET_RAW']
        item['BoundingCaps'] = []
        item['EffectiveCaps'] = []
        validator.validate('container', item, self.scope, self.image, self.config)

    def test_external_model_mount_validation(self):
        item = self.container()
        item['Mounts'][0] = {'Type': 'bind', 'Source': '/existing models',
                             'Destination': '/models-store', 'RW': False}
        item['Config']['Env'] = ['OLLAMA_CONTEXT_LENGTH=32768', 'OLLAMA_MODELS=/models-store']
        validator.validate('container', item, self.scope, self.image, self.config, '/existing models')
        with self.assertRaises(ValueError):
            validator.validate('container', item, self.scope, self.image, self.config, '/different-models')
        item['Mounts'][0]['RW'] = True
        with self.assertRaises(ValueError):
            validator.validate('container', item, self.scope, self.image, self.config, '/existing models')

    def test_bad_network_and_volume(self):
        for kind, data in [('network', {'labels': self.labels(), 'internal': False}),
                           ('network', {'internal': True}),
                           ('volume', {'Labels': self.labels(), 'Driver': 'local', 'Options': {'device': '/home'}})]:
            with self.subTest(kind=kind, data=data), self.assertRaises(ValueError):
                validator.validate(kind, data, self.scope)

    def test_bad_containers(self):
        mutations = [lambda c: c['Config']['Labels'].clear(),
                     lambda c: c['Config'].update(Image='other-image'),
                     lambda c: c['NetworkSettings']['Networks'].update(external={}),
                     lambda c: c['HostConfig'].update(Privileged=True),
                     lambda c: c['HostConfig'].update(ReadonlyRootfs=False),
                     lambda c: c['HostConfig'].update(CapAdd=['NET_ADMIN']),
                     lambda c: c['HostConfig'].update(CapDrop=['NET_RAW']),
                     lambda c: c.update(BoundingCaps=['CAP_NET_ADMIN']),
                     lambda c: c.update(EffectiveCaps=['CAP_NET_ADMIN']),
                     lambda c: c['HostConfig'].update(SecurityOpt=[]),
                     lambda c: c['HostConfig'].update(SecurityOpt=['no-new-privileges=false']),
                     lambda c: c['HostConfig'].update(SecurityOpt=['no-new-privileges:false']),
                     lambda c: c['HostConfig'].update(PortBindings={'11434/tcp': [{}]}),
                     lambda c: c['HostConfig'].update(Tmpfs={'/etc': 'rw'}),
                     lambda c: c['HostConfig'].update(Devices=[{'PathOnHost': '/dev/mem'}]),
                     lambda c: c['Mounts'][0].update(RW=True),
                     lambda c: c['Mounts'].append({'Type': 'bind', 'Destination': '/host'}),
                     lambda c: c['Config'].update(Cmd=['run', 'model']),
                     lambda c: c['Config'].update(Env=['OLLAMA_CONTEXT_LENGTH=1'])]
        for i, mutate in enumerate(mutations):
            with self.subTest(mutation=i):
                item = self.container(); mutate(item)
                with self.assertRaises(ValueError):
                    validator.validate('container', item, self.scope, self.image, self.config)


class FirewallTests(unittest.TestCase):
    def test_cloud_firewall_failure_prevents_agent_start(self):
        for box in ['claude', 'codex']:
            source = (ROOT / f'{box}-box/{box}-box').read_text()
            firewall = source.split("<<'INIT' || true\n", 1)[1].split('\nINIT\n', 1)[0]
            for fails in ['0', '1']:
                with self.subTest(box=box, fails=fails), tempfile.TemporaryDirectory() as d:
                    t = Path(d)
                    commands = {
                        'id': 'echo 0',
                        'ip': 'exit 0',
                        'iptables': '[ "$TEST_FIREWALL_FAIL" = 0 ]',
                        'ip6tables': '[ "$TEST_FIREWALL_FAIL" = 0 ]',
                        'setpriv': 'printf "%s\\n" "$@" > "$TEST_AGENT_LOG"',
                    }
                    for name, body in commands.items():
                        path = t / name
                        path.write_text('#!/bin/sh\n' + body + '\n')
                        path.chmod(0o755)
                    env = dict(os.environ, PATH=d + ':' + os.environ['PATH'],
                               TEST_FIREWALL_FAIL=fails, TEST_AGENT_LOG=str(t / 'agent.log'))
                    result = subprocess.run(['bash', '-c', firewall], env=env, capture_output=True)
                    if fails == '1':
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse((t / 'agent.log').exists())
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        args = (t / 'agent.log').read_text().splitlines()
                        self.assertIn('--bounding-set=-all', args)
                        self.assertIn('--no-new-privs', args)
                        self.assertEqual(args[-1], 'claude' if box == 'claude' else 'codex')


class LifecycleTests(unittest.TestCase):
    def test_cloud_sessions_leave_container_names_to_podman(self):
        for box in ['claude', 'codex']:
            with self.subTest(box=box):
                source = (ROOT / f'{box}-box/{box}-box').read_text()
                launch = source[source.index('exec podman run --rm -it'):]
                script = '''
PROJECT=box
PROJECT_DIR=/projects/box
MOUNT_Z=
NET_OPTS=pasta
IMAGE=test-image
FIREWALL_SCRIPT=test-firewall
ENV_ARGS=()
CODEX_ARGS=()
CLAUDE_ARGS=()
podman() { printf '%s\\0' "$@"; }
''' + launch.replace('exec podman', 'podman', 1)
                result = subprocess.run(['bash', '-eu', '-c', script], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = result.stdout.decode().split('\0')[:-1]
                self.assertEqual(args[:3], ['run', '--rm', '-it'])
                self.assertFalse(any(a == '--name' or a.startswith('--name=') for a in args))
                self.assertNotIn('--replace', args)
                self.assertEqual(args[args.index('--hostname') + 1], f'{box}-box')
                self.assertIn('/projects/box:/work/box', args)

    def test_installer_rejects_extra_arguments_before_copying(self):
        with tempfile.TemporaryDirectory() as d:
            result = subprocess.run(['bash', str(ROOT / 'install.sh')] + ['codex'] * 10 + ['unknown'],
                                    env=dict(os.environ, HOME=d), capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(Path(d, '.local/bin').exists())

    def test_installer_copies_version_and_validator(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HOME=d)
            result = subprocess.run(['bash', str(ROOT / 'install.sh'), 'all'], env=env,
                                    stdin=subprocess.DEVNULL, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(Path(d, '.config/ollama-box/validate-resource.py').is_file())
            for name in ['internet-access.py', 'internet-proxy.py', 'Containerfile.internet-proxy']:
                self.assertTrue(Path(d, '.config/ollama-box', name).is_file())
            for box in ['claude', 'codex', 'ollama']:
                result = subprocess.run([str(Path(d, '.local/bin', box + '-box')), '--version'],
                                        env=env, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(b'0.3.0', result.stdout)

    def test_ollama_creation_reuse_cleanup_and_rejection(self):
        source = (ROOT / 'ollama-box/ollama-box').read_text()
        definitions = source[:source.index('# --- ollama-box options')]
        for mode, existing, unsafe, external in [('isolated', False, False, False),
                                                 ('shared', True, False, False),
                                                 ('isolated', True, True, False),
                                                 ('isolated', False, False, True)]:
            with self.subTest(mode=mode, existing=existing, unsafe=unsafe), tempfile.TemporaryDirectory() as d:
                t = Path(d); (t / 'bin').mkdir()
                fixtures = ResourceTests()
                network = {'labels': fixtures.labels(), 'internal': not unsafe}
                data = {'network': network, 'volume': {'Labels': fixtures.labels(), 'Driver': 'local'},
                        'container': fixtures.container()}
                model_dir = t / 'existing models'
                if external:
                    model_dir.mkdir()
                    data['container']['Mounts'][0] = {'Type': 'bind', 'Source': str(model_dir),
                                                      'Destination': '/models-store', 'RW': False}
                    data['container']['Config']['Env'] = ['OLLAMA_CONTEXT_LENGTH=32768', 'OLLAMA_MODELS=/models-store']
                (t / 'fixtures.json').write_text(json.dumps(data))
                mock = t / 'bin/podman'
                mock.write_text("""#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['TEST_LOG'],'a') as f: f.write(json.dumps(args)+'\\n')
if args[:2] in [['network','exists'],['container','exists']]:
    sys.exit(0 if os.environ['TEST_EXISTING']=='1' else 1)
if len(args)>1 and args[1]=='inspect':
    if '-f' in args: print('true')
    else: print(json.dumps([json.loads(Path(os.environ['TEST_FIXTURES']).read_text())[args[0]]]))
""")
                mock.chmod(0o755)
                env = dict(os.environ, PATH=str(t / 'bin') + ':' + os.environ['PATH'],
                           OLLAMA_BOX_DIR=str(ROOT / 'ollama-box'), TEST_LOG=str(t / 'calls'),
                           TEST_EXISTING='1' if existing else '0', TEST_FIXTURES=str(t / 'fixtures.json'))
                storage = ''
                if external:
                    storage = ('\nMODELS_DIR=' + shlex.quote(str(model_dir)) +
                               '\nMODELS_MOUNT=' + shlex.quote(str(model_dir) + ':/models-store:ro') +
                               '\nMODELS_CONTAINER_PATH=/models-store\n')
                script = definitions + storage + """
NETWORK_NAME=ollama-box-net-test
OLLAMA_CONTAINER=ollama-server-test
GPU_MODE=cpu
NETWORK_MODE=""" + mode + """
trap stop_ollama_if_unused EXIT
start_ollama 32768
"""
                result = subprocess.run(['bash', '-c', script], env=env, capture_output=True)
                calls = [json.loads(line) for line in (t / 'calls').read_text().splitlines()]
                if unsafe:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(any(c[0] in ['rm', 'run'] for c in calls))
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if mode == 'isolated':
                        run = next(c for c in calls if c[0] == 'run')
                        expected_mount = str(model_dir) + ':/models-store:ro' if external else 'ollama-box-models:/models-store:ro'
                        self.assertIn(expected_mount, run)
                        if external:
                            self.assertFalse(any(c[0] == 'volume' for c in calls))
                        self.assertIn(['rm', '-f', 'ollama-server-test'], calls)
                        self.assertIn(['network', 'rm', 'ollama-box-net-test'], calls)
                    else:
                        self.assertFalse(any(c[0] in ['rm', 'run'] for c in calls))


if __name__ == '__main__':
    unittest.main()
