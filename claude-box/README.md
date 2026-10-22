# claude-box

> **Experimental / untested:** macOS and Windows/WSL2 have not been verified in practice.
> Container, networking, and security features still need to be checked there.

Claude Code isolated in a Podman container. General information, requirements, and
installation: [main README](../README.md).

## Files

| File | Installed to | Purpose |
|---|---|---|
| `claude-box` | `~/.local/bin/claude-box` | Launcher script |
| `Containerfile.claude-base` | `~/.config/claude-box/` | Base image: Node, Git, Python, Claude Code, firewall tools |
| `templates/settings.json` | `<project>/.claude/settings.json` (by hand) | No Claude attribution in commits, example rules against destructive Git commands |

## Usage

```bash
cd ~/<root-project>/<project>
claude-box               # launch
claude-box --resume      # further arguments are passed to claude
claude-box --rebuild     # rebuild images (updates Claude Code)
claude-box --help        # help
```

On first launch you log in once. If the browser link opens but no confirmation comes
back, copy the displayed code into the terminal. The login is stored in the
`claude-config` volume and applies to all projects.

On launch, the container must report that the firewall is active and the home
network is blocked, listing the blocked own networks. If it doesn't, Claude Code is
not started.

## Which image?

`claude-base` is always used, regardless of the programming language. Containerfiles
in the project are ignored. Add extra tools to the installed
`Containerfile.claude-base` and rebuild with `claude-box --rebuild`.
The included tools are listed in the [main README](../README.md#toolchains).

## How it isolates

- Only the project directory is mounted, at `/<root-project>/<project>` inside the container.
- The container briefly starts as root with four capabilities (NET_ADMIN, SETUID,
  SETGID, SETPCAP), sets up the firewall, then permanently drops all privileges and
  starts Claude Code as an ordinary user.
  If the firewall fails, Claude Code does not start.
- Firewall: private address ranges, link-local, multicast, IPv6 ULA, and the local
  subnet are blocked. The internet and DNS to the configured DNS servers are allowed.
- Claude Code runs in its default mode (Auto Mode).

## Environment variables

| Variable | Effect |
|---|---|
| `CLAUDE_BOX_ROOT` | Override the projects root directory (otherwise the saved first-run choice) |
| `CLAUDE_BOX_DIR` | Directory containing the Containerfiles |
| `CLAUDE_BOX_PCAP=1` | Capture network traffic to `~/.config/claude-box/pcap/` |
| `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` | Passed through if set |

## Verification

In a second terminal, `podman ps` shows `claude-<project>`. Run inside the container:

```bash
curl -m 3 http://192.168.1.1          # example; use your own router address: must fail
curl -sI https://api.anthropic.com    # must respond
```

`--version` shows the installed launcher version and paths; `--configure` changes
the projects root directory confirmed on first launch.

The optional settings template and its permission rules are not a security boundary.
An agent can modify files in the project; review changes before using them on the host.
