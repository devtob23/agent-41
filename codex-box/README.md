# codex-box

> **Experimental / untested:** macOS and Windows/WSL2 have not been verified in practice.
> Container, networking, and security features still need to be checked there.

Codex isolated in a Podman container. General information, requirements, and
installation: [main README](../README.md).

## Files

| File | Installed to | Purpose |
|---|---|---|
| `codex-box` | `~/.local/bin/codex-box` | Launcher script |
| `Containerfile.codex-base` | `~/.config/codex-box/` | Base image: Node, Git, Python, Codex, firewall tools |

## Logging in (once)

The normal browser login redirects to `localhost`, which does not reach the container.
Use a device code instead:

```bash
cd ~/yourProjectPath/<project>
codex-box --login
```

Open the link in your browser and enter the code. The login is stored in the
`codex-config` volume and applies to all projects. Alternatively, set
`OPENAI_API_KEY`; it is passed through.

If the device code does not work: launch `codex-box` normally, log in in the browser,
copy the address of the error page (`http://localhost:1455/…`), and send it into the
container from a second terminal. Podman assigns the container name, so look it up
first:

```bash
podman ps --format '{{.Names}}  {{.Image}}'          # the one running codex-base…
podman exec --user 1000 <container-name> curl -s 'COPIED-ADDRESS'
```

## Usage

```bash
cd ~/yourProjectPath/<project>
codex-box                                   # launch
codex-box "Explain the project structure"   # with an initial task
codex-box --rebuild                         # rebuild images
codex-box --help                            # help
```

## Project and isolation

codex-box works independently of claude-box and ollama-box. It needs only its own
Containerfiles and builds the `codex-base` images.

- Project rules live in `AGENTS.md`.
- `codex-base` is always used, regardless of the programming language.
- The included tools are listed in the [main README](../README.md#toolchains).
- Containerfiles in the project are ignored. Only the bundled Containerfiles from
  codex-box's configuration directory are built.
- Git rules can only be given as instructions in your project's `AGENTS.md`; review commits before pushing.
- The built-in Codex sandbox is disabled (`danger-full-access`); the container is the boundary.
- Approval prompts use the `on-request` mode.
- The firewall blocks private address ranges and the local subnet. After setup, the
  agent runs without special privileges and sees only the mounted project directory.

## Environment variables

| Variable | Effect |
|---|---|
| `CODEX_BOX_ROOT` | Override the projects root directory (otherwise the saved first-run choice) |
| `CODEX_BOX_DIR` | Directory containing the Containerfiles |
| `CODEX_BOX_PCAP=1` | Capture network traffic to `~/.config/codex-box/pcap/` |
| `OPENAI_API_KEY` | Passed through if set |

## Troubleshooting

**"container name … is already in use":** An earlier container for this project is
still running or was left behind.

```bash
podman ps -a --filter name=codex-
podman stop codex-<project>          # still running
podman rm codex-<project>            # exited
podman rm --storage codex-<project>  # leftover in storage ("external entity")
```

`--version` shows the installed launcher version and paths; `--configure` changes
the projects root directory confirmed on first launch.
