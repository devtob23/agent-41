#!/usr/bin/env python3
"""Validate Podman inspect JSON; fail closed on unknown or unsafe resources."""
import json
import sys


def validate(kind, item, scope, image='', config='', models_dir=''):
    labels = item.get('Labels') or item.get('labels') or {}
    if kind == 'container':
        labels = item.get('Config', {}).get('Labels') or {}
    if labels.get('io.agent-41.owner') != 'ollama-box':
        raise ValueError('missing ownership label (legacy resources must be migrated manually)')
    if kind != 'volume' and labels.get('io.agent-41.scope') != scope:
        raise ValueError('session ownership differs')
    if kind == 'network':
        if not item.get('internal', item.get('Internal', False)):
            raise ValueError('network is not internal')
    elif kind == 'volume':
        if item.get('Driver') != 'local' or item.get('Options'):
            raise ValueError('expected a plain local model volume')
    elif kind == 'container':
        cfg, host = item['Config'], item['HostConfig']
        if cfg.get('Image') != image or labels.get('ollama-box.config') != config:
            raise ValueError('image or context/GPU configuration differs')
        networks = item.get('NetworkSettings', {}).get('Networks') or {}
        if set(networks) != {scope}:
            raise ValueError('unexpected network attachment')
        if host.get('Privileged') or not host.get('ReadonlyRootfs'):
            raise ValueError('container must be unprivileged and read-only')
        if host.get('CapAdd') or item.get('EffectiveCaps'):
            raise ValueError('unexpected capabilities')
        dropped = {str(cap).upper().replace('CAP_', '', 1) for cap in host.get('CapDrop') or []}
        if (not dropped or item.get('BoundingCaps') or
                ('ALL' not in dropped and item.get('BoundingCaps') != [])):
            raise ValueError('all capabilities must be dropped')
        if not any(str(o) in ('no-new-privileges', 'no-new-privileges=true', 'no-new-privileges:true')
                   for o in host.get('SecurityOpt', [])):
            raise ValueError('no-new-privileges is missing')
        if host.get('PortBindings'):
            raise ValueError('published ports are forbidden')
        allowed_tmpfs = {'/tmp', '/root/.cache', '/root/.ollama'}
        if set(host.get('Tmpfs') or {}) - allowed_tmpfs:
            raise ValueError('unexpected tmpfs destinations')
        if any(m.get('Type') == 'tmpfs' and m.get('Destination') not in allowed_tmpfs
               for m in item.get('Mounts', [])):
            raise ValueError('unexpected tmpfs mount')
        if config.endswith('gpu=cpu') and (host.get('Devices') or host.get('DeviceRequests')):
            raise ValueError('unexpected GPU/device access in CPU mode')
        mounts = [m for m in item.get('Mounts', []) if m.get('Type') != 'tmpfs']
        if len(mounts) != 1:
            raise ValueError('unexpected mounts')
        mount = mounts[0]
        if mount.get('Destination') != '/models-store' or mount.get('RW'):
            raise ValueError('model storage must be mounted read-only at /models-store')
        if models_dir:
            if mount.get('Type') != 'bind' or mount.get('Source') != models_dir:
                raise ValueError('unexpected external model directory')
        elif mount.get('Type') != 'volume' or mount.get('Name') != 'ollama-box-models':
            raise ValueError('unexpected managed model volume')
        if cfg.get('Entrypoint') not in (['/bin/ollama'], ['/usr/bin/ollama'], ['ollama']):
            raise ValueError('unexpected server entrypoint')
        if cfg.get('Cmd') != ['serve']:
            raise ValueError('unexpected server command')
        env = dict(e.split('=', 1) for e in cfg.get('Env', []) if '=' in e)
        if env.get('OLLAMA_MODELS') != ('/models-store' if models_dir else '/models-store/models'):
            raise ValueError('unexpected model storage path')
        expected_ctx = config.split(',')[0].split('=', 1)[1]
        if env.get('OLLAMA_CONTEXT_LENGTH') != expected_ctx:
            raise ValueError('context environment differs')
    else:
        raise ValueError('unknown resource kind')


if __name__ == '__main__':
    try:
        data = json.load(sys.stdin)
        item = data[0] if isinstance(data, list) else data
        validate(sys.argv[1], item, *sys.argv[2:])
    except (ValueError, KeyError, TypeError, IndexError) as error:
        print(f'ollama-box: resource validation failed: {error}', file=sys.stderr)
        sys.exit(1)
