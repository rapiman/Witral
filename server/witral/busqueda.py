"""
Búsqueda en un proyecto: por nombre de archivo y por contenido (grep regex).
Soporta `donde`. Excluye build/.gradle/.git por defecto.

En local se recorre el árbol con Python. En remoto se delega en grep/find del
sistema vía SSH.
"""

from __future__ import annotations

import os
import re
from fnmatch import fnmatch

from .config import Lugar
from .seguridad import normalizar
from . import transporte as T


_EXCLUIR = {
    "build", ".gradle", ".git", ".witral", "node_modules",
    # Entornos/artefactos de Python y editores: recorrerlos hacía que
    # buscar_nombre/buscar_contenido se fueran a decenas de miles de líneas
    # (p. ej. .venv con miles de .pyc). Se podan como .git.
    ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "dist", ".idea", ".vscode",
}
_INCLUIR_DEFAULT = ["*.kt", "*.java", "*.xml", "*.kts", "*.gradle"]


def buscar_nombre(lugar: Lugar, proyecto: str, patron: str) -> str:
    """Busca por NOMBRE de archivo (substring o regex simple)."""
    if lugar.es_local:
        base = normalizar(lugar.raiz, proyecto)
        rx = re.compile(patron)
        out = []
        # os.walk con poda IN-PLACE de dirs excluidos: no se DESCIENDE en build/.gradle/.git/etc.
        # (antes rglob recorria TODO el arbol y filtraba despues -> se colgaba en proyectos Android).
        for raiz, dirs, archivos in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _EXCLUIR]
            for nombre in archivos:
                if rx.search(nombre):
                    ruta = os.path.join(raiz, nombre)
                    out.append(os.path.relpath(ruta, base))
        return "\n".join(sorted(out)) if out else "(sin coincidencias)"
    # remoto: find
    excl = " ".join(f"-not -path '*/{e}/*'" for e in _EXCLUIR)
    cmd = f"cd '{proyecto}' && find . -type f {excl} | grep -E '{patron}'"
    r = T.ejecutar(lugar, cmd)
    return r.salida or "(sin coincidencias)"


def buscar_contenido(lugar: Lugar, objetivo: str, patron: str,
                     incluir: list[str] | None = None,
                     antes: int = 0, despues: int = 0) -> str:
    """
    grep de contenido (regex) en un ARCHIVO o una CARPETA/proyecto.
    Si 'objetivo' es un archivo, busca solo ahí. Si es carpeta, recorre recursivo
    aplicando los globs 'incluir'.

    'antes'/'despues': líneas de CONTEXTO antes/después de cada match (como -B/-A
    de grep). Con 0 (por defecto) sale una línea por match: `ruta:linea: texto`.
    Con contexto, las líneas de contexto salen con '-' en vez de ':'
    (`ruta-linea- texto`) y los grupos separados se separan con '--', para que
    el match y su alrededor lleguen sin un `leer` posterior.
    """
    incluir = incluir or _INCLUIR_DEFAULT
    antes = max(0, antes)
    despues = max(0, despues)
    if lugar.es_local:
        base = normalizar(lugar.raiz, objetivo)
        rx = re.compile(patron)
        out = []

        def buscar_en(p, etiqueta):
            try:
                texto = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return
            lineas = texto.splitlines()
            matches = [i for i, l in enumerate(lineas) if rx.search(l)]
            if not matches:
                return
            if antes == 0 and despues == 0:
                for i in matches:
                    out.append(f"{etiqueta}:{i + 1}: {lineas[i].strip()}")
                return
            # Con contexto: unir en rangos contiguos (fusiona solapes) y emitir
            # cada grupo separado por '--', al estilo grep -A/-B.
            n = len(lineas)
            rangos: list[list[int]] = []
            for i in matches:
                a = max(0, i - antes)
                b = min(n - 1, i + despues)
                if rangos and a <= rangos[-1][1] + 1:
                    rangos[-1][1] = max(rangos[-1][1], b)
                else:
                    rangos.append([a, b])
            mset = set(matches)
            for k, (a, b) in enumerate(rangos):
                if k > 0:
                    out.append("--")
                for j in range(a, b + 1):
                    sep = ":" if j in mset else "-"
                    out.append(f"{etiqueta}{sep}{j + 1}{sep} {lineas[j].rstrip()}")

        if base.is_file():
            # Objetivo es un archivo único: ignorar los globs 'incluir'.
            buscar_en(base, base.name)
        else:
            # os.walk con poda IN-PLACE de dirs excluidos (no se desciende en build/.gradle/etc.),
            # filtrando cada archivo por los globs 'incluir' con fnmatch. Antes rglob por cada glob
            # recorria las carpetas excluidas y filtraba despues -> lento/colgado en Android.
            from pathlib import Path
            for raiz, dirs, archivos in os.walk(base):
                dirs[:] = [d for d in dirs if d not in _EXCLUIR]
                for nombre in archivos:
                    if not any(fnmatch(nombre, g) for g in incluir):
                        continue
                    p = Path(raiz) / nombre
                    buscar_en(p, p.relative_to(base))
        return "\n".join(out) if out else "(sin coincidencias)"
    # remoto: si es archivo, grep directo; si es carpeta, grep -rn con --include.
    ctx = ""
    if antes:
        ctx += f" -B {antes}"
    if despues:
        ctx += f" -A {despues}"
    chk = T.ejecutar(lugar, f"test -f '{objetivo}' && echo F || echo D")
    es_archivo = (chk.salida or "").strip() == "F"
    if es_archivo:
        cmd = f"grep -nE{ctx} '{patron}' '{objetivo}'"
    else:
        incl = " ".join(f"--include='{g}'" for g in incluir)
        excl = " ".join(f"--exclude-dir='{e}'" for e in _EXCLUIR)
        cmd = f"cd '{objetivo}' && grep -rnE{ctx} {incl} {excl} '{patron}' ."
    r = T.ejecutar(lugar, cmd)
    return r.salida or "(sin coincidencias)"
