# Agent-41

> **Experimental:** Linux is the primary target. macOS (Podman machine) and
> Windows (WSL2) are untested.

Run AI coding agents in rootless Podman containers with access to exactly one
project directory. The launchers do not mount your home directory, SSH keys, Git
credentials or other projects. The boxes are independent: install only the ones
you need.

| Box | Agent | Models | Network during a session |
|---|---|---|---|
| [claude-box](claude-box/README.md) | Claude Code | Anthropic | Internet; host and private networks blocked by an in-container firewall |
| [codex-box](codex-box/README.md) | Codex | OpenAI | Internet; host and private networks blocked by an in-container firewall |
| [ollama-box](ollama-box/README.md) | Aider, Claude Code, OpenCode, shell | Local, via Ollama | Internal network only; tool HTTP/HTTPS through a gateway with per-model permission |

Per-box guides: [claude-box](claude-box/README.md), [codex-box](codex-box/README.md),
[ollama-box](ollama-box/README.md). Manual equivalent of everything the scripts do:
[docs/MANUAL.md](docs/MANUAL.md). Security model and limits: [SECURITY.md](SECURITY.md).

## How it works

- **One project per container.** A box only starts directly inside
  `<projects-root>/<project>`, which is mounted at `/work/<project>`. The root itself,
  deeper subdirectories and paths outside the root are rejected. Paths are resolved
  first, so symlinks can't escape the check. Paths containing colons or commas are
  rejected to avoid ambiguous mount arguments.
- **Rootless, unprivileged.** Agents run as an ordinary user with no Linux
  capabilities and `no-new-privileges`. Cloud containers start as container root
  only long enough to install the firewall (`NET_ADMIN`), then drop all
  capabilities before the agent starts. `--userns=keep-id` makes files the agent
  creates belong to your host user.
- **Network.** Cloud boxes use pasta with host mapping disabled, plus iptables
  rules that reject private, link-local, multicast and the host's own networks. If
  the firewall can't be installed, the agent doesn't start. ollama-box puts the
  agent and Ollama on an internal Podman network with no route out; see [Ollama](#ollama).
- **Images.** Built on first use from the Containerfiles installed in
  `~/.config/<box>/` and reused across projects. Containerfiles inside a project are
  never built. Containers are temporary (`--rm`) and recreated on each launch; the
  tools live in the image, so nothing is reinstalled. Claude and Codex containers get
  unique names, so several sessions can run in the same project.
- **Logins** persist in the Podman volumes `claude-config` and `codex-config`, shared
  across projects of the same agent. API keys (`ANTHROPIC_API_KEY`,
  `CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`) are passed through from your
  environment if set and never written anywhere by the launchers.
- **No host Git credentials.** Credentials already inside the project can still be
  used. Review and push commits yourself.

Keep in mind: Cloud agents can send anything in the project to the internet; a container does
not prevent that.

## Requirements

- Rootless Podman 4.4+ (5.x recommended) and `passt`, which provides `pasta`.
- ollama-box also needs Python 3 on the host for resource validation and the
  internet gateway.
- Agents and language tools run in the images; Codex, Claude Code and Node.js are
  not needed on the host.

### Linux

```bash
sudo dnf install podman passt python3      # Fedora
sudo apt install podman passt python3      # Debian / Ubuntu
sudo pacman -S podman passt python         # Arch
```

`podman info` must report rootless operation.

> ⚠ macOS (Podman machine) and
> Windows (WSL2) are untested yet

### macOS

```bash
brew install podman python
podman machine init
podman machine start
```

Podman runs inside a Linux VM, so project and external model directories must be
visible in that VM. The scripts work with the system Bash 3.2. Ollama gets no GPU
acceleration in the VM.

### Windows (WSL2)

1. In PowerShell: `wsl --install -d Ubuntu`.
2. Inside Ubuntu: `sudo apt install podman passt python3 git`.
3. Clone and install this repository **inside WSL**.
4. Keep projects on the WSL filesystem; `/mnt/c/...` is much slower.
5. Put environment overrides in `~/.profile`.

The [`windows/`](windows/) wrappers can be copied to a directory on the Windows
`PATH`. They run the installed scripts through WSL in the current directory.
Configured paths must use their WSL form.

## Installation

```bash
cd agent-41
./install.sh claude         # only Claude Code
./install.sh codex          # only Codex
./install.sh ollama         # only local Ollama agents
./install.sh claude codex   # several
./install.sh all            # all three
```

Launchers go to `~/.local/bin/` (`--bin <dir>` to change), Containerfiles and helper
scripts to `~/.config/<box>/`. Changed files are backed up as `*.bak-<timestamp>`
before being replaced; saved preferences are kept. The installer needs no sudo,
builds nothing and downloads nothing; images are built on the first start of each
box. If `~/.local/bin` is not on your `PATH`, add `export PATH="$HOME/.local/bin:$PATH"`
to your shell profile.

## Quick start

```bash
mkdir -p ~/projects/my-project
cd ~/projects/my-project
claude-box               # Claude Code; log in on first use
codex-box --login        # Codex device-code login, once
codex-box                # Codex
```

> ⚠️ **Codex: log in with the device code only.** When Codex asks you to sign in,
> use the device code (or run `codex-box --login`, which starts the device-code
> login directly). The browser sign-in redirects to `localhost`, and that callback
> can't reach the container, so it never completes. Alternatively set
> `OPENAI_API_KEY`. Details: [codex-box guide](codex-box/README.md#logging-in-once).

On first start each box asks for the projects root. For local models see [Ollama](#ollama).

| Option (all boxes) | Effect |
|---|---|
| `--help` | Usage, paths and environment variables; works without setup |
| `--version` | Launcher version and paths (not the agent version inside the image) |
| `--configure` | Redo first-run setup |
| `--rebuild` | Rebuild the image, e.g. to update the agent |
| `--with <list>` | Build in extra toolchains, see [Toolchains](#toolchains) |
| `--login` | codex-box only: device-code login |

The projects root is saved in `~/.config/<box>/projects-root`.
`CLAUDE_BOX_ROOT`, `CODEX_BOX_ROOT` and `OLLAMA_BOX_ROOT` override it; without a
terminal, one of the two is required.

## Ollama

ollama-box runs a local Ollama server and an agent (`aider` by default; `claude`,
`opencode` or `shell`) in containers on an internal Podman network. Inference never
leaves that network.

```bash
ollama-box --pull qwen2.5-coder:14b              # download a model (needs internet)
ollama-box                                       # start; menu if several models
ollama-box --model qwen2.5-coder:14b --agent opencode
ollama-box --models                              # list downloaded models
ollama-box --agents                              # list agents
ollama-box run                                   # plain chat, no project, no tools
```

Context window, per-agent details and Aider/Git behaviour: [ollama-box guide](ollama-box/README.md).

### First-run choices

| Setting | Saved in `~/.config/ollama-box/` | Override | Default |
|---|---|---|---|
| Model source | `models-source` | `OLLAMA_BOX_MODELS_DIR=<dir>\|managed` | managed volume `ollama-box-models` |
| Network mode | `network-mode` | `OLLAMA_BOX_NETWORK_MODE=isolated\|shared` | isolated |
| GPU | `gpu` | `--gpu`, then `OLLAMA_BOX_GPU=nvidia\|amd\|cpu` | cpu |

Without a terminal, the defaults apply. `ollama-box --configure` asks again.

**Model source.** Either the managed volume, filled with `--pull`, or an existing
Ollama models directory: the one containing `blobs/` and `manifests/`, such as
`~/.ollama/models`, not a single GGUF file or `.ollama` itself. An existing directory
is mounted read-only and never copied, relabelled or modified, so `--pull` is
disabled for it; download new models with your host's Ollama. On SELinux systems it
must already be readable by containers; on macOS it must be visible to the VM; under
WSL use a Linux path.

**Network mode.** *Isolated* gives every session its own internal network and
Ollama server, removed on normal exit and on INT/TERM. *Shared* runs one server for
all sessions: less RAM/VRAM, but sessions can reach each other, and the server keeps
running after the last client exits. A shared server must match the selected image,
context window, GPU mode and model source; stop it deliberately when changing them.
Models are mounted read-only during inference in both modes.

**GPU.** If a GPU is detected and nothing is saved, the first session offers
`ollama-box --setup-gpu`. It checks the driver, installs the NVIDIA Container
Toolkit and CDI spec or checks the ROCm groups (each sudo step is shown and
confirmed), tests GPU access in a container (this pulls the Ollama image, several
GB) and saves the choice. Declining saves `cpu`. CPU inference works but is much
slower, especially with Claude Code and OpenCode. Details:
[ollama-box GPU](ollama-box/README.md#gpu), [manual GPU setup](docs/MANUAL.md#gpu-setup-for-ollama-box).

### Internet permission per model

Agent tools reach the internet only through an authenticated HTTP/HTTPS gateway,
with a separate credential per session. Permission is per exact model name and tag.

- **At launch** (until a permanent choice is saved): allow for this session, always
  allow, stay offline and ask on demand (default), or always offline.
- **In ask mode**, each new connection prompts on the host terminal with model,
  destination host, port and type: allow once, always allow, deny once (default) or
  always deny. A denial returns an error to the tool; the agent keeps running. An
  HTTPS approval covers one CONNECT tunnel, which may carry several requests.
  Permanent choices don't close connections that are already open.
- **Always blocked:** private, loopback, link-local and reserved destinations, even
  when allowed; DNS results are checked before connecting. Raw sockets, SSH, UDP and
  tools that ignore proxy settings get no access at all.

Choices are stored in `~/.config/ollama-box/internet-permissions/`, out of reach of
the containers. Override per run with `--internet ask|allow|deny|reset` or
`OLLAMA_BOX_INTERNET`; `reset` asks again. Without a terminal, or if the gateway
fails to start, the session stays offline.

### Resources

ollama-box labels everything it creates with `io.agent-41.*` and validates existing
networks, volumes and servers before reuse. Anything with missing or foreign labels,
a non-internal network or an unexpected configuration aborts the start; nothing is
adopted or deleted silently.

```bash
podman ps -a --filter label=io.agent-41.owner=ollama-box
podman network ls --filter label=io.agent-41.owner=ollama-box
podman volume ls --filter label=io.agent-41.owner=ollama-box
```

## Toolchains

Default images contain Node.js, TypeScript, Python, GCC/Make, Git, curl, wget, jq
and ripgrep. Further toolchains are opt-in, so their disk space is only used when
needed.
`--with` accepts **exactly the names below**, in any combination; any other
name is rejected before building.

| `--with` name | Adds |
|---|---|
| `cpp` | CMake, Ninja, pkg-config, GDB |
| `go` | Go |
| `java` | JDK, Maven |
| `rust` | Rust, Cargo |
| `ruby` | Ruby, Bundler |
| `php` | PHP, Composer |
| `perl`, `lua` | Perl, Lua |
| `all` | All of the above |

```bash
claude-box --with rust,go
export CLAUDE_BOX_WITH=rust,go          # permanent; also CODEX_BOX_WITH, OLLAMA_BOX_WITH
```

Each combination is its own image (`claude-base-go-rust`, …) kept next to the lean
one; remove unused variants with `podman rmi`. ollama-box agents are offline by
default, so their tools must be in the image.

**Languages not in the list** need a change to the box itself:

- *Locally, for all variants:* add a `RUN` install step to the installed
  Containerfile in `~/.config/<box>/` (before any `USER` line, so it runs as root),
  then `--rebuild`. The next `./install.sh`
  backs up and replaces that edit.
- *As a new `--with` name:* add a `case` entry with its packages to the `TOOLCHAINS`
  step in the box's Containerfile, and the name to `TOOLCHAINS_ALL` in the launcher
  script (the launcher validates names against it). Do this in the repository, so
  updates keep it.

## Updating

```bash
git pull
./install.sh <boxes>         # pulling alone does not update the installed launchers
claude-box --rebuild         # per box, inside a project
```

`--rebuild` removes the previous version of the image it replaces. Cached layers
may be reused, so not every package is guaranteed to be refreshed. For ollama-box
it also pulls the latest Ollama image.

## Troubleshooting

| Message (excerpt) | Cause and fix |
|---|---|
| `Podman must run rootless` / `Do not run as root` | Run as your normal user with rootless Podman; `podman info` must show `rootless: true`, also for remote connections. |
| `Podman … is too old` | Podman older than 4.4; update it. |
| `Podman is not responding` | `podman info` fails; on macOS start the VM with `podman machine start`. |
| `Note: older pasta version …` | pasta lacks the host-mapping switches; the launcher falls back to plain pasta and still installs the firewall. Update `passt` and verify host access is blocked. |
| `You are in the projects root itself` / `You are in a subfolder of a project` / `… is not inside …` | Start directly in `<projects-root>/<project>`; change the root with `--configure`. |
| `could not set up the IPv4 firewall` / `… IPv6 firewall` | iptables failed in the container, so the agent was not started. Check the host's netfilter modules and that `NET_ADMIN` is allowed for rootless containers. |
| `Unknown toolchain '…'` | Name not in the `--with` list; see [Toolchains](#toolchains). |
| `Building … failed` | Usually network or disk space; see below. Rerun with `--rebuild`. |
| `No models available` / `Model '…' is not downloaded` | `ollama-box --pull <model>`; with an external models directory, download through the host's Ollama. |
| `Unsafe or foreign resource '…'` | A network, volume or container with the expected name lacks valid `io.agent-41.*` labels or has an unexpected configuration, e.g. created by an older version. Inspect it, back up model data, then remove it so it is recreated. |
| `Test failed: Podman cannot pass the GPU through` | CDI spec missing or stale (NVIDIA: `sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`) or missing `video`/`render` groups (AMD, log in again after adding). See [manual GPU setup](docs/MANUAL.md#gpu-setup-for-ollama-box). |
| Ollama runs on CPU despite a GPU | `podman exec <server> ollama ps` shows `PROCESSOR`; check the saved choice in `~/.config/ollama-box/gpu`, then restart the server or run `--setup-gpu`. |
| `Internet permission files are missing` | Gateway files missing in `~/.config/ollama-box/`; run `./install.sh ollama` again. |

### ⚠️ Disk space ("no space left on device")

Images and models are large, and rootless Podman stores everything under its
graphroot, usually `~/.local/share/containers`.

```bash
podman info --format '{{.Store.GraphRoot}} {{.Store.GraphDriverName}}'
podman system df
df -h <graphroot>; df -i <graphroot>
```

- **Really full:** `podman image prune` or `podman system prune`. Not `--volumes`,
  which deletes logins and models. Or move `graphroot` in
  `~/.config/containers/storage.conf` and rebuild.
- **Out of inodes** (`df -i` at 100 %, typical for ext4): images are many small
  files; use more inodes or a filesystem with dynamic inodes (XFS, btrfs).
- **btrfs full despite free space:** no unallocated chunks left for metadata. Check
  `sudo btrfs filesystem usage <path>`, fix with `sudo btrfs balance start -dusage=50 <path>`.
- **Driver `vfs` instead of `overlay`:** the filesystem doesn't support rootless
  overlay (NFS, some encrypted homes), so every layer is copied in full. Use a local
  filesystem.
- **`/var/tmp` or `$TMPDIR` too small:** builds use it for temporary layers.

## Security limits

- The agent can change or delete anything in the mounted project, including Git
  hooks, build scripts and dependency directories such as `node_modules/` and
  `.venv/` that you may later run on the host. Commit first, review before running;
  `git status --ignored` also shows ignored files.
- Containers share the host kernel; keep kernel and Podman updated. Cloud agents
  can read their own login credentials and any secret inside the project.
- Image builds and model downloads run online without the cloud firewall.
- Capture cloud traffic with `CLAUDE_BOX_PCAP=1` or `CODEX_BOX_PCAP=1`; captures
  may contain sensitive data.

See [SECURITY.md](SECURITY.md) for the full model and how to report issues.

## Repository layout

```text
install.sh      per-box installer
claude-box/     launcher, Containerfile, settings template
codex-box/      launcher, Containerfile
ollama-box/     launcher, agent Containerfiles, internet gateway, resource validator
windows/        WSL2 wrappers
docs/MANUAL.md  manual setup
tests/          unit tests (mocked Podman)
```

## License and tests

[MIT](LICENSE).

Run the tests with `python3 -m unittest discover -s tests -v`.
They cover setup, installation, resource validation, mocked Podman flows,
permission prompts and the gateway; real container, GPU and firewall behaviour
still needs checking on the target system.
