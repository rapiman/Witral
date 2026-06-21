"""
Verificación de sintaxis en dos capas:

1. Capa UNIVERSAL (siempre, para todos los lenguajes): balance de paréntesis,
   corchetes y llaves, y comillas sin cerrar — ignorando lo que está dentro de
   strings y comentarios. Atrapa el error de edición más común (un símbolo de
   más o sin cerrar) sin entender el lenguaje a fondo.

2. Capa NATIVA (si la herramienta está instalada): un chequeo real con el
   verificador del lenguaje (node --check, py_compile, etc.). Se suma encima de
   la universal cuando está disponible.

El objetivo no es reemplazar al compilador, sino dar una red rápida antes de
mover/compilar: si la capa universal falla, casi siempre hay un error real.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# --- Perfiles de lenguaje por extensión -------------------------------------

@dataclass
class Lenguaje:
    nombre: str
    # Delimitadores de comentario de línea (ej. "//", "#", "--").
    coment_linea: tuple[str, ...] = ()
    # Pares de comentario de bloque (ej. ("/*", "*/"), ("<!--", "-->")).
    coment_bloque: tuple[tuple[str, str], ...] = ()
    # Caracteres que abren/cierran string. Cada uno cierra con sí mismo.
    comillas: tuple[str, ...] = ('"', "'")
    # Si el lenguaje permite escapar comillas con backslash dentro del string.
    escape_backslash: bool = True


_C_LIKE = dict(
    coment_linea=("//",),
    coment_bloque=(("/*", "*/"),),
    comillas=('"', "'", "`"),
)

EXTENSIONES: dict[str, Lenguaje] = {
    ".kt":    Lenguaje("Kotlin", **_C_LIKE),
    ".kts":   Lenguaje("Kotlin Script", **_C_LIKE),
    ".java":  Lenguaje("Java", coment_linea=("//",), coment_bloque=(("/*", "*/"),)),
    ".c":     Lenguaje("C", coment_linea=("//",), coment_bloque=(("/*", "*/"),)),
    ".h":     Lenguaje("C header", coment_linea=("//",), coment_bloque=(("/*", "*/"),)),
    ".cpp":   Lenguaje("C++", coment_linea=("//",), coment_bloque=(("/*", "*/"),)),
    ".js":    Lenguaje("JavaScript", **_C_LIKE),
    ".jsx":   Lenguaje("JSX", **_C_LIKE),
    ".ts":    Lenguaje("TypeScript", **_C_LIKE),
    ".php":   Lenguaje("PHP", coment_linea=("//", "#"),
                       coment_bloque=(("/*", "*/"),), comillas=('"', "'")),
    ".py":    Lenguaje("Python", coment_linea=("#",), comillas=('"', "'")),
    ".sql":   Lenguaje("SQL", coment_linea=("--",),
                       coment_bloque=(("/*", "*/"),), comillas=("'", '"')),
    ".html":  Lenguaje("HTML", coment_bloque=(("<!--", "-->"),), comillas=('"', "'")),
    ".xml":   Lenguaje("XML", coment_bloque=(("<!--", "-->"),), comillas=('"', "'")),
    ".css":   Lenguaje("CSS", coment_bloque=(("/*", "*/"),), comillas=('"', "'")),
    ".sh":    Lenguaje("Shell", coment_linea=("#",), comillas=('"', "'")),
    ".rb":    Lenguaje("Ruby", coment_linea=("#",), comillas=('"', "'")),
    ".pl":    Lenguaje("Perl", coment_linea=("#",), comillas=('"', "'")),
    # JSON no tiene comentarios ni comillas simples (solo dobles); la validación
    # real la hace la capa nativa con json.loads.
    ".json":  Lenguaje("JSON", comillas=('"',)),
    ".yaml":  Lenguaje("YAML", coment_linea=("#",), comillas=('"', "'")),
    ".yml":   Lenguaje("YAML", coment_linea=("#",), comillas=('"', "'")),
    ".toml":  Lenguaje("TOML", coment_linea=("#",), comillas=('"', "'")),
}


# --- Capa universal: balance de símbolos ------------------------------------

@dataclass
class Hallazgo:
    linea: int
    columna: int
    mensaje: str


_PARES = {")": "(", "]": "[", "}": "{"}
_ABRE = set("([{")


def revisar_balance(texto: str, lang: Lenguaje) -> list[Hallazgo]:
    """
    Recorre el texto carácter a carácter, saltando comentarios y strings, y
    verifica que los símbolos de agrupación abran y cierren bien anidados.
    Devuelve la lista de problemas (vacía si está balanceado).
    """
    hallazgos: list[Hallazgo] = []
    pila: list[tuple[str, int, int]] = []  # (símbolo, línea, columna)

    i = 0
    n = len(texto)
    linea = 1
    col = 0
    # Para detectar HTML/XML no usamos balance de <> (demasiado ruido); solo ()[]{}.

    def avanzar(c: str):
        nonlocal linea, col
        if c == "\n":
            linea += 1
            col = 0
        else:
            col += 1

    while i < n:
        c = texto[i]

        # ¿Comentario de bloque?
        bloque_match = None
        for ini, fin in lang.coment_bloque:
            if texto.startswith(ini, i):
                bloque_match = (ini, fin)
                break
        if bloque_match:
            ini, fin = bloque_match
            cierre = texto.find(fin, i + len(ini))
            if cierre == -1:
                hallazgos.append(Hallazgo(linea, col,
                    f"comentario de bloque sin cerrar ({ini} … {fin})"))
                break
            for k in range(i, cierre + len(fin)):
                avanzar(texto[k])
            i = cierre + len(fin)
            continue

        # ¿Comentario de línea?
        cl = None
        for marca in lang.coment_linea:
            if texto.startswith(marca, i):
                cl = marca
                break
        if cl:
            fin_linea = texto.find("\n", i)
            if fin_linea == -1:
                break
            for k in range(i, fin_linea):
                avanzar(texto[k])
            i = fin_linea
            continue

        # ¿String?
        if c in lang.comillas:
            comilla = c
            l0, c0 = linea, col
            avanzar(c)
            i += 1
            cerrado = False
            while i < n:
                ch = texto[i]
                if lang.escape_backslash and ch == "\\":
                    avanzar(ch); i += 1
                    if i < n:
                        avanzar(texto[i]); i += 1
                    continue
                if ch == comilla:
                    avanzar(ch); i += 1
                    cerrado = True
                    break
                # String que cruza salto de línea: lo permitimos (multilinea),
                # salvo comillas simples/dobles en lenguajes estrictos — pero
                # para no dar falsos positivos, toleramos.
                avanzar(ch); i += 1
            if not cerrado:
                hallazgos.append(Hallazgo(l0, c0,
                    f"comilla {comilla} sin cerrar"))
            continue

        # Símbolos de agrupación
        if c in _ABRE:
            pila.append((c, linea, col))
        elif c in _PARES:
            if not pila:
                hallazgos.append(Hallazgo(linea, col,
                    f"'{c}' de cierre sin apertura"))
            elif pila[-1][0] != _PARES[c]:
                ab, la, ca = pila[-1]
                hallazgos.append(Hallazgo(linea, col,
                    f"'{c}' no coincide con '{ab}' abierto en línea {la}"))
                pila.pop()
            else:
                pila.pop()

        avanzar(c)
        i += 1

    for ab, la, ca in pila:
        hallazgos.append(Hallazgo(la, ca, f"'{ab}' sin cerrar"))

    return hallazgos


# --- Capa nativa: verificadores reales si están instalados ------------------

import shutil
import subprocess
import os


@dataclass
class Verificador:
    # Cómo verificar: función que recibe la ruta del archivo y devuelve
    # (ok, salida). Solo se usa si 'binario' existe en el PATH.
    binario: str
    construir_cmd: object  # callable(ruta) -> list[str]


def _cmd_node(ruta):    return ["node", "--check", ruta]
def _cmd_python(ruta):  return ["python", "-m", "py_compile", ruta]
def _cmd_perl(ruta):    return ["perl", "-c", ruta]
def _cmd_php(ruta):     return ["php", "-l", ruta]
def _cmd_gcc(ruta):     return ["gcc", "-fsyntax-only", ruta]
def _cmd_ruby(ruta):    return ["ruby", "-c", ruta]


# Verificador nativo por extensión (solo se usa si el binario está instalado).
NATIVOS: dict[str, Verificador] = {
    ".js":  Verificador("node", _cmd_node),
    ".jsx": Verificador("node", _cmd_node),
    ".py":  Verificador("python", _cmd_python),
    ".pl":  Verificador("perl", _cmd_perl),
    ".php": Verificador("php", _cmd_php),
    ".c":   Verificador("gcc", _cmd_gcc),
    ".h":   Verificador("gcc", _cmd_gcc),
    ".rb":  Verificador("ruby", _cmd_ruby),
}


def verificador_disponible(ext: str) -> str | None:
    """Nombre del binario nativo para esa extensión si está en el PATH, si no None."""
    v = NATIVOS.get(ext)
    if v and shutil.which(v.binario):
        return v.binario
    return None


def correr_nativo(ext: str, ruta_local: str) -> tuple[bool, str] | None:
    """
    Corre el verificador nativo si está disponible. Devuelve (ok, salida) o None
    si no hay verificador para esa extensión / no está instalado. Solo local.
    """
    v = NATIVOS.get(ext)
    if not v or not shutil.which(v.binario):
        return None
    try:
        # input="" da un stdin con EOF inmediato: sin esto, algunos
        # verificadores (py_compile) quedan esperando stdin y se cuelgan,
        # igual que pasaba con git sobre el transporte stdio.
        p = subprocess.run(v.construir_cmd(ruta_local), capture_output=True,
                           text=True, timeout=30, input="")
        salida = (p.stdout + "\n" + p.stderr).strip()
        return (p.returncode == 0, salida)
    except Exception as e:  # noqa
        return (False, f"error al correr {v.binario}: {e}")


# Formatos de datos que se validan con una librería Python (no un binario): la
# validación corre sobre el texto directamente, así que sirve local Y remoto.
LIBRERIA: dict[str, str] = {
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
}


def validar_por_libreria(ext: str, texto: str) -> tuple[bool, str] | None:
    """
    Valida JSON/YAML/TOML parseando el texto con la librería correspondiente.
    Devuelve (ok, detalle) o None si la extensión no aplica o la librería no
    está disponible. Funciona en cualquier lugar porque opera sobre el texto.
    """
    cual = LIBRERIA.get(ext)
    if cual == "json":
        import json
        # Tolerar BOM (igual que witral lee sus configs con utf-8-sig): un BOM
        # al inicio no es un error real de JSON para este entorno.
        limpio = texto.lstrip("\ufeff")
        try:
            json.loads(limpio)
            return (True, "JSON válido.")
        except json.JSONDecodeError as e:
            return (False, f"línea {e.lineno}, col {e.colno}: {e.msg}")
    if cual == "yaml":
        try:
            import yaml
        except ImportError:
            return None  # pyyaml no instalado -> solo capa universal
        try:
            yaml.safe_load(texto)
            return (True, "YAML válido.")
        except yaml.YAMLError as e:
            return (False, str(e))
    if cual == "toml":
        try:
            import tomllib  # stdlib desde Python 3.11
        except ImportError:
            return None
        try:
            tomllib.loads(texto)
            return (True, "TOML válido.")
        except tomllib.TOMLDecodeError as e:
            return (False, str(e))
    return None
