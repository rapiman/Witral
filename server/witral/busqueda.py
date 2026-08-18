"""
Búsqueda en un proyecto: por nombre de archivo y por contenido (grep regex).
Soporta `donde`. Excluye build/.gradle/.git/entornos por defecto.

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

# Tope por defecto de coincidencias para buscar_contenido: una búsqueda amplia
# (ej. un identificador común) devolvía un muro. Se puede subir/bajar por llamada.
_MAX_RESULTADOS_DEFAULT = 200


def _regex_o_glob(patron: str):
    """
    Intenta compilar 'patron' como REGEX. Si no compila pero parece un glob
    (tiene `*`, `?` o `[...]`), lo interpreta como glob (fnmatch) — así
    `*.apk`, que como regex da "nothing to repeat", igual funciona. Devuelve
    (es_glob, rx_o_None, nota). Si es regex inválida y NO parece glob, devuelve
    (False, None, mensaje_de_error) para que el llamador lo muestre tal cual.
    """
    try:
        return False, re.compile(patron), ""
    except re.error as e:
        if any(c in patron for c in "*?["):
            return True, None, (
                f"[patrón interpretado como glob (no compilaba como regex: {e}); "
                f"para regex usar, p. ej., '\\.apk$' en vez de '*.apk'.]\n")
        return False, None, (
            f"error: '{patron}' no es una regex válida ({e}). buscar_nombre usa "
            f"REGEX: para la extensión .apk usar '\\.apk$' (o 'apk$'); un glob "
            f"tipo '*.apk' también se acepta y se interpreta como glob.")


def buscar_nombre(lugar: Lugar, proyecto: str, patron: str) -> str:
    """
    Busca por NOMBRE de archivo. Acepta REGEX; si el patrón no compila como
    regex pero parece un glob (ej. '*.apk'), se interpreta como glob.
    """
    es_glob, rx, nota = _regex_o_glob(patron)
    if rx is None and not es_glob:
        return nota  # regex inválida y no es glob: mensaje de error
    if lugar.es_local:
        base = normalizar(lugar.raiz, proyecto)
        out = []
        # os.walk con poda IN-PLACE de dirs excluidos: no se DESCIENDE en
        # build/.gradle/.git/entornos (antes rglob recorría todo y filtraba
        # después -> se colgaba en proyectos Android).
        for raiz, dirs, archivos in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _EXCLUIR]
            for nombre in archivos:
                hit = fnmatch(nombre, patron) if es_glob else rx.search(nombre)
                if hit:
                    out.append(os.path.relpath(os.path.join(raiz, nombre), base))
        cuerpo = "\n".join(sorted(out)) if out else "(sin coincidencias)"
        return nota + cuerpo
    # remoto: glob -> find -name; regex -> find | grep -E.
    excl = " ".join(f"-not -path '*/{e}/*'" for e in _EXCLUIR)
    if es_glob:
        cmd = (f"cd {T.comillas(proyecto)} && "
               f"find . -type f {excl} -name {T.comillas(patron)}")
    else:
        cmd = (f"cd {T.comillas(proyecto)} && "
               f"find . -type f {excl} | grep -E {T.comillas(patron)}")
    r = T.ejecutar(lugar, cmd)
    return nota + (r.salida or "(sin coincidencias)")


def buscar_contenido(lugar: Lugar, objetivo: str, patron: str,
                     incluir: list[str] | None = None,
                     antes: int = 0, despues: int = 0,
                     max_resultados: int = _MAX_RESULTADOS_DEFAULT) -> str:
    """
    grep de contenido (regex) en un ARCHIVO o una CARPETA/proyecto.
    Si 'objetivo' es un archivo, busca solo ahí. Si es carpeta, recorre recursivo
    aplicando los globs 'incluir'.

    'antes'/'despues': líneas de CONTEXTO antes/después de cada match (como -B/-A
    de grep). Con 0 (por defecto) sale una línea por match: `ruta:linea: texto`.
    Con contexto, las líneas de contexto salen con '-' en vez de ':'
    (`ruta-linea- texto`) y los grupos se separan con '--'.

    'max_resultados': tope de coincidencias (por defecto 200); al alcanzarlo corta
    y avisa, para que una búsqueda amplia no devuelva un muro. 0 = sin tope.
    """
    incluir = incluir or _INCLUIR_DEFAULT
    antes = max(0, antes)
    despues = max(0, despues)
    tope = max_resultados if max_resultados and max_resultados > 0 else 0
    if lugar.es_local:
        base = normalizar(lugar.raiz, objetivo)
        try:
            rx = re.compile(patron)
        except re.error as e:
            return f"error: '{patron}' no es una regex válida ({e})."
        out: list[str] = []
        estado = {"n": 0, "trunc": False}

        def buscar_en(p, etiqueta):
            if estado["trunc"]:
                return
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
                    if tope and estado["n"] >= tope:
                        estado["trunc"] = True
                        return
                    out.append(f"{etiqueta}:{i + 1}: {lineas[i].strip()}")
                    estado["n"] += 1
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
                if tope and estado["n"] >= tope:
                    estado["trunc"] = True
                    return
                if out and k > 0:
                    out.append("--")
                elif out and k == 0:
                    out.append("--")
                for j in range(a, b + 1):
                    sep = ":" if j in mset else "-"
                    out.append(f"{etiqueta}{sep}{j + 1}{sep} {lineas[j].rstrip()}")
                estado["n"] += sum(1 for j in range(a, b + 1) if j in mset)

        if base.is_file():
            # Objetivo es un archivo único: ignorar los globs 'incluir'.
            buscar_en(base, base.name)
        else:
            # os.walk con poda IN-PLACE de dirs excluidos, filtrando por 'incluir'.
            from pathlib import Path
            for raiz, dirs, archivos in os.walk(base):
                dirs[:] = [d for d in dirs if d not in _EXCLUIR]
                for nombre in archivos:
                    if not any(fnmatch(nombre, g) for g in incluir):
                        continue
                    p = Path(raiz) / nombre
                    buscar_en(p, p.relative_to(base))
                    if estado["trunc"]:
                        break
                if estado["trunc"]:
                    break
        if not out:
            return "(sin coincidencias)"
        cuerpo = "\n".join(out)
        if estado["trunc"]:
            cuerpo += (f"\n... [tope de {tope} coincidencias alcanzado; hay más. "
                       f"Afiná el patrón (p. ej. límites de palabra \\b) o subí "
                       f"max_resultados.]")
        return cuerpo
    # remoto: si es archivo, grep directo; si es carpeta, grep -rn con --include.
    ctx = ""
    if antes:
        ctx += f" -B {antes}"
    if despues:
        ctx += f" -A {despues}"
    chk = T.ejecutar(lugar, f"test -f {T.comillas(objetivo)} && echo F || echo D")
    es_archivo = (chk.salida or "").strip() == "F"
    if es_archivo:
        cmd = f"grep -nE{ctx} {T.comillas(patron)} {T.comillas(objetivo)}"
    else:
        incl = " ".join(f"--include={T.comillas(g)}" for g in incluir)
        excl = " ".join(f"--exclude-dir={T.comillas(e)}" for e in _EXCLUIR)
        cmd = (f"cd {T.comillas(objetivo)} && "
               f"grep -rnE{ctx} {incl} {excl} {T.comillas(patron)} .")
    # Cap de salida: presupuesto de líneas según el contexto pedido.
    presupuesto = tope * (2 + antes + despues) if tope else 0
    if presupuesto:
        cmd = f"{cmd} | head -n {presupuesto}"
    r = T.ejecutar(lugar, cmd)
    salida = r.salida or "(sin coincidencias)"
    if presupuesto and r.salida and (r.salida.count("\n") + 1) >= presupuesto:
        salida += (f"\n... [salida acotada a ~{tope} coincidencias; afiná el "
                   f"patrón o subí max_resultados.]")
    return salida
