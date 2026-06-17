"""
Búsqueda en un proyecto: por nombre de archivo y por contenido (grep regex).
Soporta `donde`. Excluye build/.gradle/.git por defecto.

En local se recorre el árbol con Python. En remoto se delega en grep/find del
sistema vía SSH.
"""

from __future__ import annotations

import re

from .config import Lugar
from .seguridad import normalizar
from . import transporte as T


_EXCLUIR = {"build", ".gradle", ".git", ".witral", "node_modules"}
_INCLUIR_DEFAULT = ["*.kt", "*.java", "*.xml", "*.kts", "*.gradle"]


def buscar_nombre(lugar: Lugar, proyecto: str, patron: str) -> str:
    """Busca por NOMBRE de archivo (substring o regex simple)."""
    if lugar.es_local:
        base = normalizar(lugar.raiz, proyecto)
        rx = re.compile(patron)
        out = []
        for p in base.rglob("*"):
            if any(parte in _EXCLUIR for parte in p.parts):
                continue
            if p.is_file() and rx.search(p.name):
                out.append(str(p.relative_to(base)))
        return "\n".join(sorted(out)) if out else "(sin coincidencias)"
    # remoto: find
    excl = " ".join(f"-not -path '*/{e}/*'" for e in _EXCLUIR)
    cmd = f"cd '{proyecto}' && find . -type f {excl} | grep -E '{patron}'"
    r = T.ejecutar(lugar, cmd)
    return r.salida or "(sin coincidencias)"


def buscar_contenido(lugar: Lugar, proyecto: str, patron: str,
                     incluir: list[str] | None = None) -> str:
    """grep de contenido (regex). Salida: ruta:linea: texto."""
    incluir = incluir or _INCLUIR_DEFAULT
    if lugar.es_local:
        base = normalizar(lugar.raiz, proyecto)
        rx = re.compile(patron)
        out = []
        for patron_glob in incluir:
            for p in base.rglob(patron_glob):
                if any(parte in _EXCLUIR for parte in p.parts):
                    continue
                if not p.is_file():
                    continue
                try:
                    texto = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for i, linea in enumerate(texto.splitlines(), start=1):
                    if rx.search(linea):
                        rel = p.relative_to(base)
                        out.append(f"{rel}:{i}: {linea.strip()}")
        return "\n".join(out) if out else "(sin coincidencias)"
    # remoto: grep -rn con --include
    incl = " ".join(f"--include='{g}'" for g in incluir)
    excl = " ".join(f"--exclude-dir='{e}'" for e in _EXCLUIR)
    cmd = f"cd '{proyecto}' && grep -rnE {incl} {excl} '{patron}' ."
    r = T.ejecutar(lugar, cmd)
    return r.salida or "(sin coincidencias)"
