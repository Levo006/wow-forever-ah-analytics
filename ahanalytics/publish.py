"""Veröffentlichung auf GitHub Pages über den Zweig ``gh-pages``.

Die Website (Frontend + JSON-Daten) wird nach ``site/`` exportiert. ``site/``
ist ein eigenes kleines Git-Repository, dessen Zweig ``gh-pages`` bei jeder
Veröffentlichung durch **einen einzigen neuen Commit ersetzt** und per
Force-Push hochgeladen wird. Dadurch

* wächst das Repository nicht mit jeder stündlichen Veröffentlichung,
* lädt Git nur geänderte Dateien hoch (unveränderte sind beim Server bekannt),
* bleibt der Zweig ``main`` reiner Code.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

from . import config, export

SITE_DIR = os.path.join(config.ROOT, "site")


def _git(*args, cwd, check=True):
    env = dict(os.environ, GCM_INTERACTIVE="never", GIT_TERMINAL_PROMPT="0")
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env=env)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} fehlgeschlagen:\n{res.stderr.strip() or res.stdout.strip()}")
    return res


def remote_url() -> str:
    return _git("remote", "get-url", "origin", cwd=config.ROOT).stdout.strip()


def repo_web_url(url: str) -> str:
    url = url.removesuffix(".git")
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url.split(":", 1)[1]
    return url


def publish(con, cfg=None, log=print, push=True) -> dict:
    cfg = cfg or config.load()
    origin = remote_url()
    web = repo_web_url(origin)
    git_dir = os.path.join(SITE_DIR, ".git")
    # Export (lässt site/.git unangetastet, ersetzt nur die Daten)
    keep = None
    if os.path.isdir(git_dir):
        keep = os.path.join(config.ROOT, ".site-git-tmp")
        shutil.rmtree(keep, ignore_errors=True)
        os.replace(git_dir, keep)
    try:
        if os.path.isdir(SITE_DIR):
            shutil.rmtree(SITE_DIR)
        stats = export.export_site(con, SITE_DIR, repo_url=web, log=log)
    finally:
        if keep:
            os.replace(keep, git_dir)
    if not push:
        return stats
    if not os.path.isdir(git_dir):
        _git("init", "-q", "-b", "gh-pages", cwd=SITE_DIR)
        _git("remote", "add", "origin", origin, cwd=SITE_DIR)
        _git("config", "credential.guiPrompt", "false", cwd=SITE_DIR)
        _git("config", "core.autocrlf", "false", cwd=SITE_DIR)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    _git("checkout", "-q", "--orphan", "publish-tmp", cwd=SITE_DIR)
    _git("add", "-A", cwd=SITE_DIR)
    _git("commit", "-q", "-m", f"Website-Daten {stamp}", cwd=SITE_DIR)
    _git("branch", "-D", "gh-pages", cwd=SITE_DIR, check=False)
    _git("branch", "-m", "gh-pages", cwd=SITE_DIR)
    log("Lade auf GitHub hoch (nur geänderte Dateien) …")
    _git("push", "-q", "-f", "origin", "gh-pages", cwd=SITE_DIR)
    # alte lokale Commits aufräumen, damit site/.git klein bleibt
    _git("reflog", "expire", "--expire=now", "--all", cwd=SITE_DIR, check=False)
    _git("gc", "-q", "--prune=now", cwd=SITE_DIR, check=False)
    owner_repo = web.removeprefix("https://github.com/")
    owner, _, repo = owner_repo.partition("/")
    page = f"https://{owner.lower()}.github.io/{repo}/"
    log(f"Veröffentlicht ({stamp}). In 1–2 Minuten aktuell: {page}")
    stats["url"] = page
    return stats
