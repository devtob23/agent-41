# ollama-box

> **Experimental / untested:** macOS and Windows/WSL2 have not been verified in practice.
> Container, networking, and security features still need to be checked there.

A coding agent with a local Ollama model. Inference stays on the internal network;
agent tools may use HTTP/HTTPS only after permission per model. General information,
requirements, and installation: [main README](../README.md).

## Internet permission per model

Local inference stays on the internal network. On the first **agent launch with each
model**, a choice appears:

1. **Allow for this session** — public HTTP/HTTPS connections until the session ends.
2. **Always allow for this model** — save for this model and future sessions.
3. **Stay offline; ask when access is needed** — the default; ask again at the next
   launch and when an internet connection is requested.
4. **Always keep this model offline** — save the denial and do not ask again.

Only tools that use the HTTP/HTTPS proxy can use this permission. Raw sockets, SSH,
UDP, and tools without proxy support stay offline and do not trigger an automatic
prompt. Private, local, and reserved destinations remain blocked even when access is
allowed. Each session has its own proxy credentials. The plain `ollama-box run` chat
has no external tools and remains offline.

The choice is saved per exact model name, including the tag, outside the project
under `~/.config/ollama-box/internet-permissions/`. Agent containers cannot modify
these files.

```bash
ollama-box --model qwen2.5-coder:14b --internet reset   # make the saved choice again
ollama-box --model qwen2.5-coder:14b --internet ask     # ask when needed for this invocation
ollama-box --model qwen2.5-coder:14b --internet allow   # allow for this session
ollama-box --model qwen2.5-coder:14b --internet deny    # stay offline for this session
```

Alternatively, set `OLLAMA_BOX_INTERNET=ask|allow|deny|reset`. 
Without a terminal, ask mode stays offline without saving a permanent denial. If the gateway fails to
start, the agent continues offline. Tools with permission can send project contents
to the internet. Image builds and model downloads remain separate online operations.
Gateway containers and their egress network are removed when the session ends.

## Files

| File | Installed to | Purpose |
|---|---|---|
| `ollama-box` | `~/.local/bin/ollama-box` | Launcher script |
| `Containerfile.agent-shell` | `~/.config/ollama-box/` | Base for all agents: Bash, Git, Python, curl |
| `Containerfile.agent-aider` | `~/.config/ollama-box/` | Aider agent |
| `Containerfile.agent-claude` | `~/.config/ollama-box/` | Claude Code agent with a local model |
| `Containerfile.agent-opencode` | `~/.config/ollama-box/` | OpenCode agent |
| `Containerfile.internet-proxy`, `internet-proxy.py` | `~/.config/ollama-box/` | Controlled HTTP/HTTPS gateway |
| `internet-access.py` | `~/.config/ollama-box/` | Model permissions and prompts in the host terminal |
| `validate-resource.py` | `~/.config/ollama-box/` | Validation of existing Podman resources |

Installation: `./install.sh ollama` in the repository's top-level directory.

Optional projects directory override (otherwise the first-run choice): `export OLLAMA_BOX_ROOT="$HOME/yourProjectPath"` in `~/.profile`.

## Downloading a model (once per model)

```bash
ollama-box --pull qwen2.5-coder:14b
ollama-box --models          # shows what has been downloaded
```

Only for the download does a short-lived Ollama container get internet access.
The download container has no cloud firewall; access to host and private networks
is not reliably blocked during the download. Only inference runs on the internal
offline network.

## Launching

```bash
cd ~/yourProjectPath/<project>
ollama-box                              # model selection menu, agent aider
ollama-box --model qwen2.5-coder:14b    # specify the model directly
ollama-box --agent shell                # Bash instead of aider, e.g. for testing
ollama-box --agent claude               # Claude Code with a local model
ollama-box --agent opencode             # OpenCode
ollama-box --model X -- --help          # arguments after -- are passed to the agent
```

## What happens on launch

`ollama-box` starts two containers: first Ollama in the background, which serves the
model, then the agent in your terminal. You work with the agent. It reads and changes
your project and sends requests to the model to do so. 

Without `--agent`, the default agent is **Aider**.

## Chat only, without an agent

```bash
ollama-box run qwen2.5-coder:14b    # chat with this model
ollama-box run                      # choose a model from the menu
```

Like `ollama run`: a plain chat in the terminal, offline and without access to your
projects. Works in any directory. Exit with `/bye`.

## The agents

| Agent | Autonomy | Model requirements | Context window (default) |
|---|---|---|---|
| `aider` | low, you choose the files | low | 32768 |
| `opencode` | high | medium | 32768 |
| `claude` | high | high, needs reliable tool calls | 65536 |
| `shell` | – | – | 32768 |

For `claude` and `opencode`, choose a model with tool support, for example a coding
model such as `qwen2.5-coder:14b` or larger. Small models often fail there.

**Claude Code** talks to Ollama here, not to Anthropic. It needs no login, sends
nothing to Anthropic, and does not add itself to commits. Auto Mode is not available
in this setup because it requires an Anthropic model. Claude Code therefore asks
before making changes; `Shift+Tab` switches the mode.

**OpenCode** receives its configuration from the selected model at launch.
The Ollama provider is preinstalled during the build so that offline work is possible
without downloading packages.

## Context window

How much text the model sees at once. Too small, and the model sees only part of your
code or the agent aborts in the middle of its work. Too large, and GPU memory or RAM
is insufficient. Ollama itself uses only a very small window and silently discards
excess text, so the box always sets a larger value.

| Where | Default |
|---|---|
| all agents, `ollama-box run`, `--models` | 32768 |
| `claude` | 65536 |

Your own value for all:

```bash
export OLLAMA_BOX_CONTEXT=16384     # less memory, e.g. without a GPU
export OLLAMA_BOX_CONTEXT=65536     # more context, needs more memory
```

The value applies to Ollama and is also passed to the agent. Aider would otherwise set
the window itself for each request; the box fixes it through a settings file. If
Ollama is already running with a different value for another project, the running
container is reused and a warning is printed.

Whether everything fits into GPU memory is shown by `podman exec <ollama-server-name> ollama ps`
(column `PROCESSOR`, see [GPU](#gpu)).

If only one model is downloaded, it is used without a menu.
Default model without a menu: `export OLLAMA_BOX_MODEL=qwen2.5-coder:14b`

## Bundled agents

Only `shell`, `aider`, `claude`, and `opencode` are supported.
Their bundled Containerfiles are built from `~/.config/ollama-box/`.
Containerfiles in the project are ignored. Extra tools can be added to the installed
Containerfiles and built with `ollama-box --rebuild`.

## GPU

Without a GPU, Ollama computes on the CPU. This works but is slow, especially with
OpenCode and Claude Code, which send very long requests. With a GPU, Ollama offloads
the model into GPU memory and responds many times faster.

**Important:** Having a GPU installed is not enough. A container sees it only if it is
explicitly passed through. Setup takes care of that:

```bash
ollama-box --setup-gpu
```

Setup

1. detects whether an NVIDIA card with a driver or an AMD card with ROCm is present,
2. lets you choose: NVIDIA, AMD, or no GPU,
3. **NVIDIA:** checks the driver, offers to install the NVIDIA Container Toolkit
   (apt, dnf, zypper, or pacman), generates the device description for Podman (CDI), and
   tests whether a container sees the card,
4. **AMD:** checks `/dev/kfd` and offers to add your user to the `video` and
   `render` groups,
5. saves the choice in `~/.config/ollama-box/gpu`.

Before each command using `sudo`, it shows the command and asks for confirmation. On the
first launch of `ollama-box`, setup is offered if a GPU is detected (`install.sh`
downloads nothing). Declining is saved as CPU. All steps by hand:
[docs/MANUAL.md](../docs/MANUAL.md#gpu-setup-for-ollama-box).

**Changing the choice:**

| Method | Effect |
|---|---|
| `ollama-box --setup-gpu` | set up again and save |
| `ollama-box --gpu nvidia` (or `amd`, `cpu`) | for this launch only |
| `export OLLAMA_BOX_GPU=nvidia` | for the shell, overrides the saved choice |

Order: `--gpu`, then `OLLAMA_BOX_GPU`, then the saved choice, otherwise CPU.
If the choice changes, `ollama-box` restarts Ollama on the next invocation, unless
another project is currently using it; in that case a warning is shown.

**Checking whether the GPU is used** (while an agent is running and after the first request):

```bash
podman exec <ollama-server-name> ollama ps
```

The `PROCESSOR` column should show `100% GPU`. A mixed `CPU/GPU` means the model plus
context window do not fit entirely into GPU memory; choose a smaller model or a smaller
`OLLAMA_BOX_CONTEXT`.

**Platforms:**

- Linux: NVIDIA and AMD
- Windows (WSL2): NVIDIA, provided the Windows driver supports WSL; toolkit inside the WSL distribution
- macOS: not possible, Ollama runs on the CPU in the Podman machine

Only the Ollama container gets the GPU. The agent container still reaches only Ollama
over the internal network.

## What the agent sees

| Area | Status |
|---|---|
| Files | only the one project |
| Internet | HTTP/HTTPS through the gateway after model permission; otherwise blocked |
| Home network and your machine | blocked |
| Reachable | Ollama and, if applicable, the gateway; in shared mode also other sessions on the same network |
| Privileges | no special privileges, read-only filesystem except the project, `/tmp`, and home |

## Aider and Git

> ⚠️ ** Aider makes no automatic commits and does not add itself as author or co-author.
> Still check the message after your first commit; Aider has renamed these settings
> before.

## Sessions and resource validation

The default is a separate server with an internal network per invocation. Sharing can
be chosen explicitly on first launch; Enter means isolated. Python 3 on the host
validates existing resources. Unknown or unsafe resources are not adopted.
Details and migration of old volumes: [main README](../README.md#resources).

`podman ps --filter label=io.agent-41.owner=ollama-box` shows the current server name.

## Existing models

On first launch, the box asks about existing Ollama models. The default is No
(managed volume).

Details: [existing models](../README.md#first-run-choices).
