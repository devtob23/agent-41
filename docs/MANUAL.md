# Manual setup

> **Experimental / untested:** macOS and Windows/WSL2 have not been tested in practice.
> Container, networking, and security functions still need verification on those platforms.

This guide describes every step that `install.sh` and the boxes perform automatically,
so you can do it by hand. The only exception is starting the agent container itself:
the respective box (`claude-box`, `codex-box`, `ollama-box`) does that, because security
checks, firewall, and dropping privileges work together there.

Contents:

1. [Install Podman](#1-install-podman)
2. [Copy files](#2-copy-files)
3. [Set the projects directory](#3-set-the-projects-directory)
4. [Build images](#4-build-images)
5. [Authenticate](#5-authenticate)
6. [Download models for ollama-box](#6-download-models-for-ollama-box)
7. [GPU setup for ollama-box](#gpu-setup-for-ollama-box)
8. [Project files](#8-project-files)
9. [Disable Git hooks](#9-disable-git-hooks)
10. [Updating and removing](#10-updating-and-removing)

---

## 1. Install Podman

Podman 4.4 or later is required, 5.x is recommended, running rootless.

```bash
# Fedora
sudo dnf install podman passt python3
# Debian / Ubuntu
sudo apt install podman passt python3
# Arch
sudo pacman -S podman passt python
# macOS
brew install podman python && podman machine init && podman machine start
```

Windows: in PowerShell run `wsl --install -d Ubuntu`, then continue inside Ubuntu as for Debian/Ubuntu.

Check:

```bash
podman --version
podman info | grep rootless        # must show rootless: true
```

## 2. Copy files

`install.sh` does this. By hand, in the repository directory:

```bash
mkdir -p ~/.local/bin

# claude-box
mkdir -p ~/.config/claude-box
cp claude-box/claude-box ~/.local/bin/ && chmod +x ~/.local/bin/claude-box
cp claude-box/Containerfile.claude-base ~/.config/claude-box/

# codex-box
mkdir -p ~/.config/codex-box
cp codex-box/codex-box ~/.local/bin/ && chmod +x ~/.local/bin/codex-box
cp codex-box/Containerfile.codex-base ~/.config/codex-box/

# ollama-box
mkdir -p ~/.config/ollama-box
cp ollama-box/ollama-box ~/.local/bin/ && chmod +x ~/.local/bin/ollama-box
cp ollama-box/Containerfile.* ollama-box/*.py ~/.config/ollama-box/
```

If `~/.local/bin` is not in your `PATH`, add it in `~/.profile`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 3. Set the projects directory

Each box starts only directly inside a subdirectory of its projects directory. The default
is `~/yourProjectPath`. Add to `~/.profile` (also `~/.profile` under WSL2):

```bash
export CLAUDE_BOX_ROOT="$HOME/yourProjectPath"
export CODEX_BOX_ROOT="$HOME/yourProjectPath"
export OLLAMA_BOX_ROOT="$HOME/yourProjectPath"
```

Then log in again or run `source ~/.profile`.

## 4. Build images

Each box does this on first launch or with `--rebuild`. The order matters, because
images build on one another. The last argument is the directory containing the files.

**claude-box**

```bash
podman build -t claude-base  -f ~/.config/claude-box/Containerfile.claude-base  ~/.config/claude-box
```

**codex-box**

```bash
podman build -t codex-base  -f ~/.config/codex-box/Containerfile.codex-base  ~/.config/codex-box
```

**ollama-box**

```bash
podman pull docker.io/ollama/ollama:latest          # AMD: docker.io/ollama/ollama:rocm
podman build -t ollama-agent-shell    -f ~/.config/ollama-box/Containerfile.agent-shell    ~/.config/ollama-box
podman build -t ollama-agent-aider    -f ~/.config/ollama-box/Containerfile.agent-aider    ~/.config/ollama-box
podman build -t ollama-agent-claude   -f ~/.config/ollama-box/Containerfile.agent-claude   ~/.config/ollama-box
podman build -t ollama-agent-opencode -f ~/.config/ollama-box/Containerfile.agent-opencode ~/.config/ollama-box
```

Build only the agents you use; `ollama-agent-shell` is always required.

Containerfiles in a project are ignored. If you need additional tools, add them to the
installed Containerfiles under `~/.config/<box>/` and start the respective box
with `--rebuild`.

## 5. Authenticate

- **claude-box:** on the first launch of `claude-box`, inside the container. If no
  confirmation comes back from the browser, copy the displayed code into the terminal.
  Alternatively, set `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`; the box passes them through.
- **codex-box:** `codex-box --login` (device code), or alternatively set `OPENAI_API_KEY`.
- **ollama-box:** no authentication.

Login data is stored in the Podman volumes `claude-config` and `codex-config`.

## 6. Download models for ollama-box

`ollama-box --pull <model>` does this. By hand, with a short-lived Ollama container,
which is the only one that gets internet access:

```bash
podman volume create ollama-box-models
podman run -d --rm --name ollama-box-pull \
  --cap-drop=ALL --security-opt no-new-privileges \
  -v ollama-box-models:/root/.ollama docker.io/ollama/ollama:latest
podman exec -it ollama-box-pull ollama pull qwen2.5-coder:14b
podman exec ollama-box-pull ollama list
podman rm -f ollama-box-pull
```

## GPU setup for ollama-box

`ollama-box --setup-gpu` does this. Without these steps, Ollama runs on the CPU,
even if a GPU is installed.

### NVIDIA

**1. Check the driver.** This must list your card:

```bash
nvidia-smi -L
```

**2. Install the NVIDIA Container Toolkit** (on the host, not in the container).
Source: NVIDIA Container Toolkit installation guide.

```bash
# Ubuntu / Debian
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Fedora / RHEL
curl -s -L https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo \
  | sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo
sudo dnf install -y nvidia-container-toolkit

# openSUSE
sudo zypper ar https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo
sudo zypper --gpg-auto-import-keys install -y nvidia-container-toolkit

# Arch / Manjaro
sudo pacman -S nvidia-container-toolkit
```

The toolkit is open source (Apache-2.0) and works locally. Installing from NVIDIA's
package repository means trusting their signing key.

**3. Generate the device description for Podman (CDI):**

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list          # shows, among others, nvidia.com/gpu=all
```

Run this again after every driver update. `nvidia-ctk runtime configure` is only needed
for Docker, not for Podman.

**4. Test:**

```bash
podman run --rm --device nvidia.com/gpu=all --entrypoint nvidia-smi docker.io/ollama/ollama:latest -L
```

**5. Save the selection and restart Ollama:**

```bash
mkdir -p ~/.config/ollama-box
echo nvidia > ~/.config/ollama-box/gpu
podman rm -f <ollama-server-name>        # if it is still running without the GPU
```

### AMD

```bash
ls -l /dev/kfd /dev/dri                       # must exist (amdgpu, ROCm)
sudo usermod -aG video,render "$USER"         # then log out and back in
echo amd > ~/.config/ollama-box/gpu
podman rm -f <ollama-server-name>
```

`ollama-box` then uses the `docker.io/ollama/ollama:rocm` image and passes `/dev/kfd`,
`/dev/dri`, and your user's groups (`--group-add keep-groups`) through to the
Ollama container.

### Windows (WSL2) and macOS

- **WSL2:** The NVIDIA driver for Windows makes the card available in WSL. Set up the
  toolkit and CDI as above inside the WSL distribution.
- **macOS:** not possible. Ollama runs on the CPU in the Podman machine.

### Check

After an agent's first request:

```bash
podman exec <ollama-server-name> ollama ps                          # PROCESSOR: 100% GPU
podman logs <ollama-server-name> 2>&1 | grep -iE "inference compute|gpu"
```

## 8. Project files

- **claude-box:** copy `claude-box/templates/settings.json` to
  `<project>/.claude/settings.json` and adjust it. This `settings.json` disables the
  Claude attribution in commits and forbids destructive Git commands.
- **codex-box:** rules go in `<project>/AGENTS.md`. The Git rules can only be stated
  there as instructions, not enforced technically.
- Add to the project's `.gitignore`: `.claude/settings.local.json`

## 9. Disable Git hooks

The agent could create hooks in `.git/hooks/` that later run on your host.
To disable them for all repositories:

```bash
mkdir -p ~/.config/git/hooks
git config --global core.hooksPath ~/.config/git/hooks
```

## 10. Updating and removing

**Updating:** copy the new files as in step 2, then rebuild the images as in
step 4 (or run `<box> --rebuild`).

**Removing** a box, for example `ollama-box`:

```bash
rm ~/.local/bin/ollama-box
rm -r ~/.config/ollama-box
podman rm -f <ollama-server-name>
podman rmi $(podman images --format '{{.Repository}}' | grep -E 'ollama-agent-')
podman network rm <ollama-network-name>
podman volume rm ollama-box-models     # deletes the downloaded models
```

Likewise for the other boxes:

| Box | Images | Volume (login data) |
|---|---|---|
| `claude-box` | `claude-base` | `claude-config` |
| `codex-box` | `codex-base` | `codex-config` |

Deleting a volume removes the stored login; the next launch requires authenticating
again.

## Local first-run configuration

The project path and the Ollama network mode are asked for on first launch. Details,
environment variables, and `--configure` are in the [main README](../README.md#quick-start).
Ollama needs Python 3 on the host for resource validation.

Server and network names depend on the session. Before running manual commands, look them
up with `podman ps -a --filter label=io.agent-41.owner=ollama-box` and
`podman network ls --filter label=io.agent-41.owner=ollama-box`, and
replace the placeholders. Stop a shared server only when no sessions are active.

## Internet permissions for Ollama tools

The agent box asks for internet permission per model. Permanent decisions are stored
outside the project. Options and limits are in the
[Ollama guide](../ollama-box/README.md#internet-permission-per-model).
The gateway is built from `Containerfile.internet-proxy` when needed. After updates,
also install the new Python helpers and this Containerfile.
