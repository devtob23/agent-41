# Security policy

## Supported versions

Security fixes target the latest source and installed launcher release. Check
`claude-box --version`, `codex-box --version`, or `ollama-box --version`.
After updating the repository, run `./install.sh <box>` again. An installed copy
is not updated by editing or pulling this repository. Rebuild images separately.

## Reporting a vulnerability

Use the repository's GitHub Security tab to report a vulnerability privately
when private vulnerability reporting is enabled. If it is unavailable, open a
minimal issue asking the maintainer for a private reporting channel; omit exploit
details, credentials, project contents, and network captures until a private
channel is available. No response-time guarantee is currently offered.

## Intended boundaries

Run with rootless Podman under an ordinary user account. The project directory is
writable; agents may modify or delete everything in it. Commit or back up work.
The launchers build installed Containerfiles from their configuration directory,
never Containerfiles found in a mounted project. Installed scripts, configuration
files, images, package registries, and model sources must be trusted.

Claude and Codex require internet access. Their startup firewall is intended to
block private/local networks before dropping privileges. Startup uses NET_ADMIN,
SETUID, SETGID, and SETPCAP; SETPCAP allows clearing the capability bounding set.
All are dropped before the agent runs. Public destinations and explicit DNS
exceptions remain available. Project contents and agent credentials
can be sent to external services. Host Git credentials are not mounted, but
credentials already present in the project can still be used. The Codex internal
sandbox is disabled: the container supplies the boundary.

Ollama defaults to a separate internal network and server for each invocation.
Its optional shared mode permits network communication between sessions and
shares the inference server; use it only among mutually trusted projects. The
shared server remains running after clients exit. Models are stored in one
labelled local volume, mounted read-only for inference, or a user-confirmed
existing Ollama models directory mounted read-only. External model directories
are never copied, relabelled, or modified; downloads to them are disabled. Managed
downloads use a separate internet-connected container with write access to the
managed volume. Image builds and model downloads are online operations, outside
offline inference isolation. The download container does not install the cloud
firewall; host/private-network access is not guaranteed to be blocked during downloads.
Separate inference servers may consume separate copies of model RAM/VRAM.

Ollama agent tools may use an optional HTTP/HTTPS gateway after host-controlled
per-model consent. On first agent use of a model, the user can allow this session,
allow permanently, ask on connection requests (default), or deny permanently.
Connection prompts allow one connection, permanent permission, one denial, or
permanent denial. Denials return a tool error and do not terminate the agent.
A CONNECT approval covers the tunnel, potentially including multiple HTTP requests;
the gateway cannot infer a model's purpose or approve individual encrypted actions.

The inference server and agent remain on the internal network. Only a separate,
unprivileged gateway joins an internet-connected egress network. It has no project,
model, or host-configuration mounts, drops capabilities, uses a read-only filesystem,
and disables IP forwarding. Proxy authentication uses a separate random credential
per session, including in shared mode. Its stdin/stdout control channel belongs to
the host supervisor; agents cannot save or approve their own model preferences.
The proxy rejects private, loopback, link-local, reserved, and known IPv6 transition
destinations, validates all DNS answers, and connects to validated IPs directly.
Denied requests are not resolved or connected. Granted public connections can send
project contents and secrets to external services. DNS for approved hostnames is
resolved using the gateway's configured resolver.

Clients that bypass HTTP/HTTPS proxy settings do not gain internet access or
trigger approval prompts. Raw sockets, SSH, and UDP are not supported by this
permission flow. A saved denial or a noninteractive ask-mode session starts no
gateway. A missing answer or gateway failure denies access while preserving local
work. Saved choices are keyed by exact model name/tag in host configuration;
changes govern future connections and future sessions, not already open tunnels.
Other concurrently running sessions retain their current session permissions.
Pure Ollama chat has no external tools and stays offline. Real Podman networking
and gateway cleanup checks are still required before claiming verified isolation.

Existing Ollama networks, model volumes, and server containers are inspected
before reuse. Unknown ownership, non-internal networks, unexpected image,
configuration, mounts, ports, capabilities, or privilege settings cause an abort.
Legacy unlabelled resources are rejected, not automatically deleted or adopted.
Back up and migrate existing model data before replacing a legacy volume.
Validation prevents accidental unsafe reuse; it cannot defend against a hostile
process running as the same host user and controlling Podman concurrently.

Containers share the host kernel. They do not protect against kernel/runtime
vulnerabilities, resource exhaustion, or changes later executed on the host
through Git hooks, dependencies, build scripts, and editor plugins. Keep Podman,
the kernel, images, and tools updated. Login volumes are shared across projects
for each cloud agent. Project-root and network preferences are local plain-data
files, not shell scripts; environment overrides are trusted user configuration.

## Verification status

Automated checks cover launcher behavior and resource-validation fixtures.
macOS and Windows/WSL2 are experimental and have not been tested in practice.
Passing these checks is not proof of isolation on every Podman, networking,
GPU, or operating-system configuration. Real container/firewall integration
checks are required for the platform on which the toolkit is deployed.

The settings template and agent permission rules are guidance, not isolation
boundaries. Dependencies and agent packages are downloaded during builds without
pinned versions; review upstream changes before distributing built images. This
repository's MIT license covers its own code, not third-party agents, models, or
packages. Preserve their license notices and follow their distribution terms.
