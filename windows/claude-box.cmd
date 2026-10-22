@echo off
rem EXPERIMENTAL / UNTESTED: Windows/WSL2 support has not been verified in practice.
rem claude-box for Windows: runs the script in WSL2 in the current folder.
rem Requirement: WSL2 with Podman and claude-box set up (see README.md).
rem Folders under C:\ appear in WSL as /mnt/c/... and are slower there.
rem --exec calls bash directly; -l loads ~/.profile (PATH and settings).
wsl.exe --cd "%CD%" --exec bash -lc "claude-box \"$@\"" claude-box %*
