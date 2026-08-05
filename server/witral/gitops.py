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
    """
    git show de un commit o de un BLOB (archivo en una rama/commit).
    - ref tipo "rama:ruta" o "commit:ruta" (contiene ':') -> vuelca el CONTENIDO
      de ese archivo en esa rama/commit (sin --stat). Útil para comparar la
      versión de un archivo entre ramas en un merge.
    - ref de commit (sin ':') -> muestra el commit con --stat (resumen).
    """
    if ":" in ref:
        # Es un blob (rama:ruta): volcar el contenido del archivo, sin --stat.
        return _git(lugar, repo, ["show", ref])
    return _git(lugar, repo, ["show", ref, "--stat"])


# --- Transporte de cambios --------------------------------------------------

def pull(lugar: Lugar, repo: str) -> T.Resultado:
    return _git(lugar, repo, ["pull", "--ff-only"])


def fetch(lugar: Lugar, repo: str) -> T.Resultado:
    """git fetch de todos los remotos. No modifica el working tree."""
    return _git(lugar, repo, ["fetch", "--all"])


def commit(lugar: Lugar, repo: str, mensaje: str = "", todos: bool = False,
           merge: bool = False) -> T.Resultado:
    """
    git commit. Con 'mensaje' usa -m. Con 'merge'=True y SIN mensaje, sella un
    merge en curso usando el mensaje automático que git ya preparó (--no-edit,
    toma MERGE_MSG). Si se da mensaje, se usa ese aunque merge=True.
    """
    if merge and not mensaje:
        args = ["commit", "--no-edit"]
    elif mensaje:
        args = ["commit", "-m", mensaje]
    else:
        return T.Resultado(1, "", "Falta 'mensaje' (o usar merge=True para sellar "
                                  "un merge con el mensaje automático).")
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


def untracked(lugar: Lugar, repo: str) -> list[str]:
    """Lista de archivos sin trackear (los NUEVOS que un 'add .' agregaría)."""
    r = _git(lugar, repo, ["status", "--porcelain"])
    if not r.ok:
        return []
    return [l[3:].strip() for l in r.salida.splitlines() if l.startswith("?? ")]


def publicar(lugar: Lugar, repo: str, mensaje: str, rutas: list[str] | None = None,
             empujar: bool = True, forzar: bool = False,
             excluir: list[str] | None = None) -> str:
    """
    Ciclo de commit completo en una pasada: status -> add -> diff (staged) ->
    commit -> push. Para y reporta si un paso falla. Muestra el diff (--stat)
    para no perder el punto de control antes del commit. Devuelve un resumen
    legible de cada paso.
    'rutas': qué agregar (por defecto todo, "."). 'excluir': pathspecs que NO
    se agregan (se traducen a ':(exclude)ruta'; sirve para dejar afuera
    archivos sueltos del working tree). 'empujar': si False, solo commitea
    local (no hace push). 'forzar': push con --force-with-lease.
    """
    rutas = rutas or ["."]
    if excluir:
        rutas = rutas + [f":(exclude){e}" for e in excluir]
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

    # Aviso de polizones: archivos NUEVOS (untracked) que el add va a llevar.
    # Solo aplica cuando se agrega todo ('.'), no con rutas explícitas.
    nuevos = untracked(lugar, repo) if "." in rutas else []
    if nuevos:
        partes.append("[NUEVOS] untracked que entran al add: " + ", ".join(nuevos) +
                      "\n(Si alguno no corresponde, usar 'excluir' o 'rutas'.)")

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


def clone(lugar: Lugar, url: str, destino: str, rama: str = "",
          timeout: int = 300) -> T.Resultado:
    """
    Clona 'url' en 'destino'. A diferencia de las demás operaciones git, el
    repo destino todavía no existe, así que no se usa `git -C`: se pasa el
    destino como argumento de `git clone`. En local el destino se normaliza
    contra la raíz autorizada (no se puede clonar fuera de ella); en remoto se
    interpreta en el lugar remoto. 'rama' opcional clona solo esa rama (--branch).
    Timeout amplio porque traer un repo puede tardar.
    """
    dst = str(normalizar(lugar.raiz, destino)) if lugar.es_local else destino
    args = ["git", "clone"]
    if rama:
        args += ["--branch", rama]
    args += [url, dst]
    return T.ejecutar(lugar, args, timeout=timeout)


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


# --- Conflictos de merge -----------------------------------------------------

def _rel_repo(repo: str, archivo: str) -> str:
    """Ruta del archivo relativa a la raíz del lugar. 'archivo' viene relativo
    al repo (como lo lista git); 'repo' es relativo a la raíz del lugar."""
    a = archivo.replace("\\", "/").strip("/")
    r = repo.replace("\\", "/").rstrip("/")
    return f"{r}/{a}" if r and r != "." else a


def _parsear_conflictos(texto: str) -> list[dict]:
    """Hunks de conflicto de merge. Cada hunk: dict con ini/fin (índices
    0-based de las líneas de marcador <<<<<<< y >>>>>>>), ours/theirs (listas
    de líneas ORIGINALES, con su \\r si el archivo es CRLF) y etiquetas.
    Soporta diff3: el bloque base (|||||||) se descarta al resolver."""
    lineas = texto.split("\n")
    hunks: list[dict] = []
    i = 0
    while i < len(lineas):
        ln = lineas[i].rstrip("\r")
        if ln.startswith("<<<<<<<"):
            et_ours = ln[7:].strip()
            ours: list = []
            theirs: list = []
            destino: list | None = ours
            fin = -1
            et_theirs = ""
            j = i + 1
            while j < len(lineas):
                lj = lineas[j].rstrip("\r")
                if lj.startswith("|||||||"):
                    destino = None  # bloque base (diff3): no es de ningún lado
                elif lj == "=======" and destino is not theirs:
                    destino = theirs
                elif lj.startswith(">>>>>>>"):
                    et_theirs = lj[7:].strip()
                    fin = j
                    break
                elif destino is not None:
                    destino.append(lineas[j])
                j += 1
            if fin < 0:
                break  # marcador sin cierre: no seguir parseando a ciegas
            hunks.append({"ini": i, "fin": fin, "ours": ours, "theirs": theirs,
                          "et_ours": et_ours, "et_theirs": et_theirs})
            i = fin + 1
        else:
            i += 1
    return hunks


def _vista_lado(lado: list, tope: int = 8) -> list[str]:
    """Vista previa de un lado del hunk: entero si es corto; si no, primeras 4
    + conteo + últimas 2 (suficiente para elegir lado sin volcar 178 líneas)."""
    v = [ln.rstrip("\r") for ln in lado]
    if len(v) <= tope:
        return [f"    {x}" for x in v]
    return ([f"    {x}" for x in v[:4]]
            + [f"    …({len(v) - 6} líneas más)…"]
            + [f"    {x}" for x in v[-2:]])


def conflictos_listar(lugar: Lugar, repo: str) -> T.Resultado:
    """Archivos del repo con conflicto sin resolver (estado U)."""
    return _git(lugar, repo, ["diff", "--name-only", "--diff-filter=U"])


def conflictos(lugar: Lugar, repo: str, archivo: str) -> str:
    """Hunks de conflicto de un archivo, numerados, con vista previa por lado."""
    from . import archivos as A
    texto = A.leer(lugar, _rel_repo(repo, archivo))
    hunks = _parsear_conflictos(texto)
    if not hunks:
        return f"{archivo}: sin marcadores de conflicto."
    partes = [f"{archivo}: {len(hunks)} hunk(s) de conflicto:"]
    for n, h in enumerate(hunks, 1):
        partes.append(
            f"\nHUNK {n} (líneas {h['ini'] + 1}-{h['fin'] + 1}) — "
            f"ours <{h['et_ours']}> {len(h['ours'])} líneas / "
            f"theirs <{h['et_theirs']}> {len(h['theirs'])} líneas")
        partes.append("  ours:")
        partes += _vista_lado(h["ours"])
        partes.append("  theirs:")
        partes += _vista_lado(h["theirs"])
    partes.append("\nResolver: git_resolver(repo, archivo, lado=\"ours\"|"
                  "\"theirs\"|\"ambos\", hunk=N); hunk=0 = todos.")
    return "\n".join(partes)


def resolver(lugar: Lugar, repo: str, archivo: str, hunk: int, lado: str) -> str:
    """Reemplaza hunk(s) de conflicto por el lado elegido, con backup. Las
    líneas sobrevivientes conservan su EOL original (solo caen los marcadores
    y el lado descartado)."""
    from . import archivos as A
    if lado not in ("ours", "theirs", "ambos"):
        return "error: lado debe ser \"ours\", \"theirs\" o \"ambos\"."
    ruta = _rel_repo(repo, archivo)
    data = A._leer_bytes(lugar, ruta)
    texto = A._decodificar(data)
    lineas = texto.split("\n")
    hunks = _parsear_conflictos(texto)
    if not hunks:
        return f"{archivo}: sin marcadores de conflicto (nada que resolver)."
    if hunk < 0 or hunk > len(hunks):
        return f"error: hunk {hunk} no existe ({archivo} tiene {len(hunks)})."
    elegidos = hunks if hunk == 0 else [hunks[hunk - 1]]
    A._backup(lugar, ruta, data)
    hechas = []
    for h in reversed(elegidos):  # de abajo hacia arriba: los índices no corren
        reemplazo = (h["ours"] if lado == "ours" else
                     h["theirs"] if lado == "theirs" else
                     h["ours"] + h["theirs"])
        lineas[h["ini"]: h["fin"] + 1] = reemplazo
        hechas.append(f"líneas {h['ini'] + 1}-{h['fin'] + 1} -> {lado} "
                      f"({len(reemplazo)} líneas)")
    A._escribir_bytes(lugar, ruta, "\n".join(lineas).encode("utf-8"))
    restantes = len(hunks) - len(elegidos)
    cierre = (f"Quedan {restantes} hunk(s) sin resolver en el archivo."
              if restantes else
              "Sin hunks restantes: marcar resuelto con git_add.")
    return (f"Resuelto en {archivo}: " + "; ".join(reversed(hechas)) +
            f". {cierre} (backup hecho)")
