# A CLEAN machine, for checking that a tide release actually installs.
#
# This walks the FRONT DOOR: `git clone` + `./install.sh`. That is how the people
# tide is handed to actually get it, so that is what gets checked. (Homebrew is a
# secondary update channel; the release artifact's pip-installability is checked
# separately at the end, without it being the way in.)
#
# Docker is Linux, so what it proves is real: the Python floor, that install.sh
# finds an interpreter and puts a working `tide` on PATH for a NON-root user, that
# `tide init` and `tide adopt` produce a working project, that the board renders,
# that self-update behaves on a clone install, and that a served surface is
# reachable from the host through a published port.
#
# What it CANNOT prove, and does not pretend to: the launchd service, spawning
# Terminal/Orca windows, Claude Code itself, and the macOS Keychain. Those are
# mac-only and stay a manual check on a mac.
#
# Built and run by packaging/docker/run.sh — never by hand.

FROM python:3.12-slim

# git: the front door is a clone, and tide adopt does `git init` + commits.
# curl: the HTTP leg checks the served board from inside the container too, so a
#       failure is caught even when nobody is looking at a browser.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# tide's board speaks Russian; without a UTF-8 locale the check's own output
# mangles it and a real encoding bug would be indistinguishable from noise.
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

# A release install must work for a NON-root user — root masks permission bugs.
RUN useradd --create-home --shell /bin/bash tester
USER tester
WORKDIR /home/tester

# Committing needs an identity; a fresh machine has none.
RUN git config --global user.email "tester@example.com" \
 && git config --global user.name "tide release check" \
 && git config --global init.defaultBranch main

# The source under test arrives as a git BUNDLE — a single file holding real
# history, which `git clone` treats exactly like a remote. That makes the checkout
# a genuine clone with an origin, so `tide self-update`'s clone path (git pull →
# reinstall) is under test rather than stubbed.
ARG BUNDLE=tide.bundle
COPY --chown=tester:tester ${BUNDLE} /home/tester/tide.bundle
COPY --chown=tester:tester smoke.sh /home/tester/smoke.sh

# The front door, exactly as a person would walk through it.
RUN git clone --quiet /home/tester/tide.bundle /home/tester/tide
WORKDIR /home/tester/tide
RUN ./install.sh

# install.sh's venv fallback links into ~/.local/bin and tells you to add it.
ENV PATH="/home/tester/.local/bin:${PATH}"

EXPOSE 8765
ENTRYPOINT ["/bin/bash", "/home/tester/smoke.sh"]
