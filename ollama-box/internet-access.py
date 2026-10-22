#!/usr/bin/env python3
"""Host-owned per-model consent and terminal supervisor for the internet gateway."""
import argparse
from collections import deque
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import secrets
import select
import signal
import subprocess
import sys
import termios
import time
import tty


def display(value):
    """Do not interpret control characters supplied by models or remote clients."""
    return str(value).encode('unicode_escape').decode('ascii')


class Policy:
    def __init__(self, config, model):
        self.model = model
        key = hashlib.sha256(model.encode()).hexdigest()
        self.path = Path(config) / 'internet-permissions' / (key + '.json')

    def load(self):
        try:
            with self.path.open() as source:
                fcntl.flock(source, fcntl.LOCK_SH)
                value = json.load(source)
            if value['model'] == self.model and value['policy'] in ('allow', 'deny'):
                return value['policy']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return 'ask'

    def save(self, policy):
        if policy not in ('allow', 'deny'):
            raise ValueError('Invalid saved policy')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        data = (json.dumps({'model': self.model, 'policy': policy}) + '\n').encode()
        # Readers share this lock; failed/incomplete writes fall back to ask mode.
        # Updating the same inode also works on filesystems that cannot replace it.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            view = memoryview(data)
            while view:
                count = os.write(fd, view)
                if not count:
                    raise OSError('Incomplete preference write')
                view = view[count:]
            os.ftruncate(fd, len(data))
            os.fsync(fd)
        except OSError:
            try:
                os.ftruncate(fd, 0)
            except OSError:
                pass
            raise
        finally:
            os.close(fd)

    def reset(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def initial_policy(policy, override='', interactive=None, read=input, write=print):
    if override == 'reset':
        try:
            policy.reset()
        except OSError:
            write('Could not clear the saved choice; asking for this session anyway.')
        mode = 'ask'
    elif override in ('ask', 'allow', 'deny'):
        return override
    else:
        mode = policy.load()
    if mode != 'ask':
        return mode
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive:
        return 'ask'  # No terminal: requests are denied without saving a decision.
    write('Internet access for model ' + display(policy.model))
    write('Access applies to this model’s agent tools. Local inference stays offline.')
    write('1) Allow for this session')
    write('2) Always allow for this model')
    write('3) Stay offline; ask when access is needed (default)')
    write('4) Always keep this model offline')
    while True:
        try:
            choice = read('Choose [1-4, default 3]: ').strip() or '3'
        except (EOFError, KeyboardInterrupt):
            return 'ask'
        if choice in ('1', '2', '3', '4'):
            if choice in ('2', '4'):
                remember(policy, 'allow' if choice == '2' else 'deny')
            return {'1': 'allow', '2': 'allow', '3': 'ask', '4': 'deny'}[choice]
        write('Choose 1, 2, 3, or 4.')


def remember(policy, mode):
    try:
        policy.save(mode)
    except OSError:
        print('ollama-box: could not save the preference; it applies to this session only.', file=sys.stderr)


def connection_decision(policy, mode, choice):
    if choice in ('2', '4'):
        mode = 'allow' if choice == '2' else 'deny'
        remember(policy, mode)
    return choice in ('1', '2'), mode


class Gateway:
    def __init__(self, config, network, session, rebuild=False):
        self.config = Path(config)
        self.network = network
        self.name = 'ollama-box-gateway-' + session
        self.egress = 'ollama-box-egress-' + session
        self.image = 'localhost/ollama-internet-proxy:latest'
        self.token = secrets.token_hex(32)
        self.process = None
        self.network_owned = False
        self.container_owned = False
        self.rebuild = rebuild

    def start(self):
        if self.rebuild or subprocess.run(['podman', 'image', 'exists', self.image]).returncode:
            subprocess.run(['podman', 'build', '-t', self.image, '-f',
                            str(self.config / 'Containerfile.internet-proxy'), str(self.config)], check=True)
        # Never adopt or delete an existing resource, even in the unlikely event of a name collision.
        subprocess.run(['podman', 'network', 'create', '--label', 'io.agent-41.owner=ollama-box',
                        '--label', 'io.agent-41.scope=' + self.network, self.egress],
                       stdout=subprocess.DEVNULL, check=True)
        self.network_owned = True
        if subprocess.run(['podman', 'container', 'exists', self.name]).returncode == 0:
            raise RuntimeError('The session gateway name is already in use.')
        command = ['podman', 'run', '--rm', '-i', '--name', self.name,
                   '--network', self.network, '--network', self.egress,
                   '--label', 'io.agent-41.owner=ollama-box',
                   '--label', 'io.agent-41.scope=' + self.network,
                   '--cap-drop=ALL', '--security-opt', 'no-new-privileges', '--read-only',
                   '--pids-limit', '128', '--sysctl', 'net.ipv4.ip_forward=0',
                   '--sysctl', 'net.ipv6.conf.all.forwarding=0',
                   '--env', 'OLLAMA_PROXY_TOKEN', self.image]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        bufsize=0, env=dict(os.environ, OLLAMA_PROXY_TOKEN=self.token))
        self.container_owned = True
        if not select.select([self.process.stdout], [], [], 20)[0]:
            raise RuntimeError('Internet gateway did not become ready.')
        ready = self.process.stdout.readline(4096)
        if not ready or json.loads(ready).get('event') != 'ready':
            raise RuntimeError('Internet gateway failed to start.')

    def environment(self, ollama_host):
        proxy = 'http://box:' + self.token + '@' + self.name + ':3128'
        bypass = ','.join(['localhost', '127.0.0.1', '::1', ollama_host])
        return {key: value for names, value in [
            (('HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy'), proxy),
            (('NO_PROXY', 'no_proxy'), bypass)] for key in names}

    def reply(self, request, allow):
        try:
            self.process.stdin.write((json.dumps({'id': request['id'], 'allow': allow}) + '\n').encode())
        except (OSError, ValueError, AttributeError):
            pass  # A lost control channel must leave the agent running offline.

    def close(self):
        def remove(command):
            try:
                subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            except (OSError, subprocess.SubprocessError):
                print('ollama-box: gateway cleanup incomplete; inspect labelled resources.', file=sys.stderr)

        if self.process is not None and self.process.stdin is not None:
            self.process.stdin.close()
            self.process.stdin = None
        if self.container_owned:
            remove(['podman', 'rm', '-f', self.name])
            self.container_owned = False
        if self.process is not None:
            try:
                self.process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate()
            self.process = None
        if self.network_owned:
            remove(['podman', 'network', 'rm', self.egress])
            self.network_owned = False


def proxy_command(command, environment):
    # Insert flags before the image; the launcher supplies an explicit marker.
    marker = command.index('--ollama-box-image')
    additions = []
    for key, value in environment.items():
        additions.extend(['--env', key + '=' + value])
    return command[:marker] + additions + command[marker + 1:]


def supervise(command, gateway, policy, mode):
    """Give the agent its own PTY so its input cannot consume host approval answers."""
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    master = slave = None
    original = None
    previous_winch = None
    process = None
    requests = deque()
    active = None
    answer = bytearray()
    wire = bytearray()
    buffered = bytearray()

    def output(data):
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    def resize(*_):
        if master is not None:
            size = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b'\0' * 8)
            fcntl.ioctl(master, termios.TIOCSWINSZ, size)

    def child_terminal():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    try:
        if interactive:
            master, slave = pty.openpty()
            resize()
            process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                       preexec_fn=child_terminal)
            os.close(slave)
            slave = None
            original = termios.tcgetattr(sys.stdin)
            tty.setraw(sys.stdin)
            previous_winch = signal.signal(signal.SIGWINCH, resize)
        else:
            process = subprocess.Popen(command)
        while process.poll() is None:
            if active is None and requests:
                request = requests.popleft()
                if mode != 'ask' or not interactive:
                    gateway.reply(request, mode == 'allow')
                else:
                    active = (request, time.monotonic())
                    output(('\r\nInternet request for ' + display(policy.model) + ': ' +
                            display(request['protocol']) + ' ' + display(request['host']) + ':' +
                            str(request['port']) + '\r\n'
                            '1) Allow this connection\r\n2) Always allow for this model\r\n'
                            '3) Deny this connection; ask next time (default)\r\n'
                            '4) Always deny for this model\r\nChoose [1-4, default 3]: ').encode())
            if active and time.monotonic() - active[1] > 110:
                gateway.reply(active[0], False)
                active = None
                answer.clear()
                output(b'\r\nNo answer; access denied. Continuing offline.\r\n' + buffered)
                buffered.clear()
            readers = [gateway.process.stdout]
            if interactive:
                readers.extend([master, sys.stdin.fileno()])
            ready, _, _ = select.select(readers, [], [], .1)
            if gateway.process.stdout in ready:
                chunk = os.read(gateway.process.stdout.fileno(), 65536)
                if not chunk:
                    output(b'\r\nInternet gateway stopped; continuing offline.\r\n')
                    if buffered:
                        output(buffered)
                    # Drop the gateway from the loop and simply forward the remaining terminal.
                    return forward_offline(process, master, interactive)
                wire.extend(chunk)
                while b'\n' in wire:
                    line, _, remainder = wire.partition(b'\n')
                    wire = bytearray(remainder)
                    request = json.loads(line)
                    if request.get('event') == 'request':
                        requests.append(request)
            if interactive and master in ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                if active:
                    buffered.extend(chunk)
                    if len(buffered) > 1048576:
                        del buffered[:-1048576]
                else:
                    output(chunk)
            if interactive and sys.stdin.fileno() in ready:
                chunk = os.read(sys.stdin.fileno(), 1024)
                if not chunk:
                    break
                if active:
                    for char in chunk:
                        if char in (10, 13):
                            choice = answer.decode() or '3'
                            if choice not in ('1', '2', '3', '4'):
                                output(b'\r\nChoose 1, 2, 3, or 4: ')
                                answer.clear()
                                continue
                            allowed, mode = connection_decision(policy, mode, choice)
                            gateway.reply(active[0], allowed)
                            active = None
                            answer.clear()
                            output(b'\r\n' + (b'Connection allowed.\r\n' if allowed else
                                              b'Access denied. Continue working offline.\r\n') + buffered)
                            buffered.clear()
                            break  # Never send pasted approval input to the agent.
                        if char in (3, 4):
                            answer[:] = b'3'
                        elif char in (8, 127):
                            answer.clear()
                        elif char in (49, 50, 51, 52):
                            answer[:] = bytes([char])
                else:
                    os.write(master, chunk)
        if buffered:
            output(buffered)
        if master is not None:
            while select.select([master], [], [], 0)[0]:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                output(data)
        return process.wait(timeout=5)
    finally:
        if original is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, original)
        if previous_winch is not None:
            signal.signal(signal.SIGWINCH, previous_winch)
        for fd in (master, slave):
            if fd is not None:
                os.close(fd)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def forward_offline(process, master, interactive):
    while process.poll() is None:
        if not interactive:
            time.sleep(.1)
            continue
        ready, _, _ = select.select([master, sys.stdin.fileno()], [], [], .1)
        for fd in ready:
            try:
                data = os.read(fd, 65536)
            except OSError:
                return process.wait(timeout=5)
            if not data:
                return process.wait(timeout=5)
            if fd == master:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            else:
                os.write(master, data)
    return process.wait()


def main():
    def terminate(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--network', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--ollama-host', required=True)
    parser.add_argument('--internet', choices=['ask', 'allow', 'deny', 'reset'], default=None)
    parser.add_argument('--rebuild', action='store_true')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    policy = Policy(args.config, args.model)
    mode = initial_policy(policy, args.internet or '')
    print('ollama-box: internet access for ' + display(args.model) + ': ' + mode, flush=True)
    if mode == 'deny' or (mode == 'ask' and not sys.stdin.isatty()):
        return subprocess.call(proxy_command(command, {}))
    gateway = Gateway(args.config, args.network, args.session, args.rebuild)
    try:
        try:
            gateway.start()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            print('ollama-box: internet unavailable; continuing offline: ' + display(error), file=sys.stderr)
            gateway.close()
            return subprocess.call(proxy_command(command, {}))
        return supervise(proxy_command(command, gateway.environment(args.ollama_host)), gateway, policy, mode)
    finally:
        gateway.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
