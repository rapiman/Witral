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


def fetch(lugar: Lugar, repo: str) -> T.Resultado:
    """git fetch de todos los remotos. No modifica el working tree."""
    return _git(lugar, repo, ["fetch", "--all"])


def commit(lugar: Lugar, repo: str, mensaje: str, todos: bool = False) -> T.Resultado:
    args = ["commit", "-m", mensaje]
    if todos:
        args.insert(1, "-a")
    return _git(lugar, repo, args)


def push(lugar: Lugar, repo: str, forzar: bool = False) -> T.Resultado:
    base = ["push", "--force-with-lease"] if forzar else ["push"]
    r = _git(lugar, repo, base)
    # Primer push de una rama sin upstream: configurarlo y reintentar.
    if not r.ok and "no upstream branch" in (r.error + r.salida):
        rama = _git(lugar, repo, ["rev-parse", "--abbrev-ref", "HEAD"])
        nombre_rama = rama.salida.strip() or "main"
        extra = ["--force-with-lease"] if forzar else []
        return _git(lugar, repo, ["push", *extra, "--set-upstream", "origin", nombre_rama])
    return r


def add(lugar: Lugar, repo: str, rutas: list[str]) -> T.Resultado:
    return _git(lugar, repo, ["add", *rutas])


def publicar(lugar: Lugar, repo: str, mensaje: str, rutas: list[str] | None = None,
             empujar: bool = True, forzar: bool = False) -> str:
    """
    Ciclo de commit completo en una pasada: status -> add -> diff (staged) ->
    commit -> push. Para y reporta si un paso falla. Muestra el diff (--stat)
    para no perder el punto de control antes del commit. Devuelve un resumen
    legible de cada paso.
    'rutas': qué agregar (por defecto todo, "."). 'empujar': si False, solo
    commitea local (no hace push). 'forzar': push con --force-with-lease.
    """
    rutas = rutas or ["."]
    partes = []

    # 1. Estado previo.
    st = status(lugar, repo)
    if not st.ok:
        return f"[status] error: {st.error or st.salida}"
    cuerpo_st = (st.salida or "").strip()
    # Si solo está la línea de rama (## ...) sin archivos, no hay nada que commitear.
    lineas_cambio = [l for l in cuerpo_st.splitlines() if not l.startswith("##")]
    if not lineas_cambio:
        return f"Nada para commitear en {repo} ({lugar.nombre}). Estado:\n{cuerpo_st}"
    partes.append(f"[1/5 status]\n{cuerpo_st}")

    # 2. Add.
    a = add(lugar, repo, rutas)
    if not a.ok:
        return "\n\n".join(partes) + f"\n\n[2/5 add] error: {a.error or a.salida}"
    partes.append(f"[2/5 add] OK ({' '.join(rutas)})")

    # 3. Diff staged (--stat): el punto de control, qué se va a commitear.
    d = diff(lugar, repo, ["--cached", "--stat"])
    partes.append(f"[3/5 diff staged]\n{(d.salida or '(sin cambios)').strip()}")

    # 4. Commit.
    c = commit(lugar, repo, mensaje)
    if not c.ok:
        return "\n\n".join(partes) + f"\n\n[4/5 commit] error: {c.error or c.salida}"
    partes.append(f"[4/5 commit]\n{(c.salida or '').strip()}")

    # 5. Push (opcional).
    if not empujar:
        partes.append("[5/5 push] omitido (empujar=False): commit solo local.")
        return "\n\n".join(partes)
    p = push(lugar, repo, forzar)
    if not p.ok:
        return ("\n\n".join(partes) +
                f"\n\n[5/5 push] error: {p.error or p.salida}\n"
                f"(El commit quedó local; revisá el error de push.)")
    partes.append(f"[5/5 push]\n{(p.salida or p.error or 'OK').strip()}")
    return "\n\n".join(partes)


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


# --- Identidad (autor de commits) -------------------------------------------

def set_identidad(lugar: Lugar, repo: str, nombre: str, email: str) -> T.Resultado:
    """Fija el autor (user.name/user.email) local a este repo."""
    rn = _git(lugar, repo, ["config", "user.name", nombre])
    if not rn.ok:
        return rn
    re = _git(lugar, repo, ["config", "user.email", email])
    if not re.ok:
        return re
    return T.Resultado(0, f"Identidad fijada: {nombre} <{email}>", "")


def get_identidad(lugar: Lugar, repo: str) -> T.Resultado:
    """Lee el autor actual (user.name/user.email) del repo."""
    n = _git(lugar, repo, ["config", "user.name"])
    e = _git(lugar, repo, ["config", "user.email"])
    nombre = n.salida.strip() or "(sin definir)"
    email = e.salida.strip() or "(sin definir)"
    return T.Resultado(0, f"{nombre} <{email}>", "")
