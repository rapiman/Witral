"""
Git sobre repos dentro de un lugar. Soporta el eje `donde` (el repo puede estar
en un server). Lectura libre; transporte de cambios (pull/push/commit) y
destructivo (reset --hard) los gobierna la capa de tools vía confirmación.

Se invoca el binario `git` con el repo como cwd. En local, subprocess; en
remoto, vía SSH con `cd <repo> && git ...`.
"""

from __future__ import annotations

from .config import Lugar
from .seguridad import normalizar
from . import transporte as T


def _git(lugar: Lugar, repo: str, args: list[str], timeout: int = 20) -> T.Resultado:
    if lugar.es_local:
        repo = str(normalizar(lugar.raiz, repo))
    return T.ejecutar(lugar, ["git", "-C", repo, *args], timeout=timeout)


# --- Lectura (libre) --------------------------------------------------------

def status(lugar: Lugar, repo: str) -> T.Resultado:
    return _git(lugar, repo, ["status", "-sb"])


def log(lugar: Lugar, repo: str, n: int = 15) -> T.Resultado:
    return _git(lugar, repo, ["log", f"-{n}", "--oneline", "--decorate"])


def diff(lugar: Lugar, repo: str, args: list[str] | None = None) -> T.Resultado:
    return _git(lugar, repo, ["diff", *(args or [])])


def branch(lugar: Lugar, repo: str) -> T.Resultado:
    return _git(lugar, repo, ["branch", "-vv"])


def show(lugar: Lugar, repo: str, ref: str) -> T.Resultado:
    return _git(lugar, repo, ["show", ref, "--stat"])


# --- Transporte de cambios --------------------------------------------------

def pull(lugar: Lugar, repo: str) -> T.Resultado:
    return _git(lugar, repo, ["pull", "--ff-only"])


def commit(lugar: Lugar, repo: str, mensaje: str, todos: bool = False) -> T.Resultado:
    args = ["commit", "-m", mensaje]
    if todos:
        args.insert(1, "-a")
    return _git(lugar, repo, args)


def push(lugar: Lugar, repo: str) -> T.Resultado:
    return _git(lugar, repo, ["push"])


def add(lugar: Lugar, repo: str, rutas: list[str]) -> T.Resultado:
    return _git(lugar, repo, ["add", *rutas])


# --- Destructivo ------------------------------------------------------------

def reset_hard(lugar: Lugar, repo: str, ref: str = "HEAD") -> T.Resultado:
    return _git(lugar, repo, ["reset", "--hard", ref])


# --- Inicialización / remotos -----------------------------------------------

def init(lugar: Lugar, repo: str, rama: str = "main") -> T.Resultado:
    """git init + rama inicial. El directorio 'repo' debe existir."""
    r = _git(lugar, repo, ["init"])
    if not r.ok:
        return r
    # Renombrar la rama por defecto (compatible con git previo a 2.28).
    # En un repo sin commits puede no aplicar; si falla, no es fatal.
    rb = _git(lugar, repo, ["branch", "-M", rama])
    salida = (r.salida + "\n" + rb.salida).strip()
    return T.Resultado(0, salida, rb.error.strip())


def remote_add(lugar: Lugar, repo: str, nombre: str, url: str) -> T.Resultado:
    """Agrega un remoto (git remote add <nombre> <url>)."""
    return _git(lugar, repo, ["remote", "add", nombre, url])


def remote_list(lugar: Lugar, repo: str) -> T.Resultado:
    """Lista remotos con sus URLs (git remote -v)."""
    return _git(lugar, repo, ["remote", "-v"])
