"""
Acciones de archivo, con el eje `donde`.

En local se opera directo sobre disco (acotado a la raíz del lugar). En remoto
se opera vía SFTP. La edición ofrece dos modos —literal y por línea— con
validación en dos fases, backup automático y preservación del fin de línea
(CRLF/LF), heredando el comportamiento del puente PowerShell anterior pero
ahora en Python.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .config import Lugar
from .seguridad import normalizar
from . import transporte as T


# --- Helpers de lectura/escritura cruda (local o remoto) -------------------

def _leer_bytes(lugar: Lugar, ruta: str) -> bytes:
    if lugar.es_local:
        p = normalizar(lugar.raiz, ruta)
        return p.read_bytes()
    return T.leer_remoto(lugar, ruta)


def _escribir_bytes(lugar: Lugar, ruta: str, data: bytes) -> None:
    if lugar.es_local:
        p = normalizar(lugar.raiz, ruta)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    else:
        T.escribir_remoto(lugar, ruta, data)


def _detectar_eol(texto: str) -> str:
    """Devuelve el fin de línea predominante: '\\r\\n' o '\\n'."""
    crlf = texto.count("\r\n")
    lf = texto.count("\n") - crlf
    return "\r\n" if crlf >= lf and crlf > 0 else "\n"


def _decodificar(data: bytes) -> str:
    return data.decode("utf-8", "replace")


# --- Lectura ----------------------------------------------------------------

def leer(lugar: Lugar, ruta: str) -> str:
    """Archivo completo como texto. Para archivos chicos."""
    return _decodificar(_leer_bytes(lugar, ruta))


def leer_rango(lugar: Lugar, ruta: str, desde: int, hasta: int) -> str:
    """
    Líneas [desde, hasta] (1-indexado, inclusive), numeradas. Es la forma
    correcta de mirar archivos grandes: pedir un tramo, no el archivo entero.
    """
    if desde < 1 or hasta < desde:
        raise ValueError("Rango inválido: desde>=1 y hasta>=desde.")
    texto = _decodificar(_leer_bytes(lugar, ruta))
    lineas = texto.splitlines()
    seleccion = lineas[desde - 1: hasta]
    ancho = len(str(desde + len(seleccion) - 1))
    return "\n".join(
        f"{str(desde + i).rjust(ancho)}\t{linea}"
        for i, linea in enumerate(seleccion)
    )


def buscar_en_archivo(lugar: Lugar, ruta: str, patron: str) -> str:
    """Números de línea donde aparece 'patron' (regex) dentro de UN archivo."""
    import re
    rx = re.compile(patron)
    texto = _decodificar(_leer_bytes(lugar, ruta))
    out = []
    for i, linea in enumerate(texto.splitlines(), start=1):
        if rx.search(linea):
            out.append(f"{i}\t{linea}")
    return "\n".join(out) if out else "(sin coincidencias)"


# --- Escritura simple -------------------------------------------------------

def escribir(lugar: Lugar, ruta: str, contenido: str) -> str:
    """Crea o sobrescribe el archivo entero."""
    _escribir_bytes(lugar, ruta, contenido.encode("utf-8"))
    return f"Escrito: {ruta} ({len(contenido)} chars) en {lugar.nombre}"


def anexar(lugar: Lugar, ruta: str, contenido: str) -> str:
    """Agrega al final sin reescribir todo (lee+concatena+escribe)."""
    try:
        actual = _leer_bytes(lugar, ruta)
    except FileNotFoundError:
        actual = b""
    _escribir_bytes(lugar, ruta, actual + contenido.encode("utf-8"))
    return f"Anexado a {ruta} en {lugar.nombre}"


# --- Backup -----------------------------------------------------------------

def _backup(lugar: Lugar, ruta: str, data: bytes) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    if lugar.es_local:
        p = normalizar(lugar.raiz, ruta)
        bakdir = Path(lugar.raiz) / ".witral" / "bak"
        bakdir.mkdir(parents=True, exist_ok=True)
        destino = bakdir / f"{p.name}.{ts}.bak"
        destino.write_bytes(data)
        return str(destino)
    else:
        destino = f"{ruta}.{ts}.bak"
        T.escribir_remoto(lugar, destino, data)
        return destino


# --- Edición: tipos de operación -------------------------------------------

@dataclass
class EdicionLiteral:
    viejo: str
    nuevo: str


@dataclass
class EdicionLinea:
    desde: int
    hasta: int
    nuevo: str


@dataclass
class EdicionAnclada:
    """
    Edición por rango CON verificación de ancla: antes de reemplazar el rango
    [desde, hasta], comprueba que su contenido actual coincide con 'ancla'
    (ignorando espacios al borde y diferencias de CRLF). Si no coincide, aborta
    sin tocar el archivo. Une la inmunidad a CRLF de la edición por línea con una
    red de seguridad contra perder la cuenta de líneas.
    """
    desde: int
    hasta: int
    ancla: str
    nuevo: str


class EdicionError(Exception):
    pass


def _norm_ancla(s: str) -> list[str]:
    """Normaliza para comparar ancla: por línea, sin espacios al borde, sin vacías al final."""
    lineas = [ln.strip() for ln in s.replace("\r\n", "\n").split("\n")]
    while lineas and lineas[-1] == "":
        lineas.pop()
    return lineas


def _aplicar_ancladas(texto: str, ediciones: list[EdicionAnclada], eol: str) -> str:
    lineas = texto.split(eol)
    total = len(lineas)
    for ed in ediciones:
        if ed.desde < 1 or ed.hasta < ed.desde or ed.hasta > total:
            raise EdicionError(
                f"Rango inválido {ed.desde}-{ed.hasta} (archivo tiene {total} líneas)."
            )
        actual = _norm_ancla(eol.join(lineas[ed.desde - 1: ed.hasta]))
        esperado = _norm_ancla(ed.ancla)
        if actual != esperado:
            raise EdicionError(
                f"El contenido en {ed.desde}-{ed.hasta} no coincide con el ancla "
                f"esperada (no se editó nada).\n"
                f"--- esperado ---\n" + "\n".join(esperado) +
                f"\n--- actual ---\n" + "\n".join(actual)
            )
    # Reusar la mecánica de líneas: convertir a EdicionLinea ya validadas.
    return _aplicar_lineas(
        texto, [EdicionLinea(e.desde, e.hasta, e.nuevo) for e in ediciones], eol
    )


def _aplicar_literal(texto: str, ed: EdicionLiteral) -> str:
    # Normalizar saltos en ambos lados a LF: el 'texto' ya viene en LF desde
    # editar(), pero el 'viejo'/'nuevo' que llegan podrían traer CRLF. Así la
    # comparación es inmune al tipo de salto de línea.
    viejo = ed.viejo.replace("\r\n", "\n")
    nuevo = ed.nuevo.replace("\r\n", "\n")
    ed = EdicionLiteral(viejo, nuevo)
    n = texto.count(ed.viejo)
    if n == 0:
        # Si 'nuevo' ya está presente, lo más probable es que la edición ya se
        # haya aplicado antes: avisarlo en vez de un seco "no aparece".
        if ed.nuevo and ed.nuevo in texto:
            raise EdicionError(
                "El bloque 'viejo' no aparece, PERO el contenido 'nuevo' ya está "
                "presente en el archivo: probablemente esta edición ya se aplicó. "
                "Verificá con leer_rango antes de reintentar."
            )
        raise EdicionError("El bloque 'viejo' no aparece en el archivo.")
    if n > 1:
        raise EdicionError(
            f"El bloque 'viejo' aparece {n} veces (ambiguo). Usá edición por línea."
        )
    return texto.replace(ed.viejo, ed.nuevo, 1)


def _aplicar_lineas(texto: str, ediciones: list[EdicionLinea], eol: str) -> str:
    lineas = texto.split(eol)
    total = len(lineas)
    # Validar rangos.
    for ed in ediciones:
        if ed.desde < 1 or ed.hasta < ed.desde or ed.hasta > total:
            raise EdicionError(
                f"Rango inválido {ed.desde}-{ed.hasta} (archivo tiene {total} líneas)."
            )
    # Aplicar de mayor a menor para no correr índices.
    for ed in sorted(ediciones, key=lambda e: e.desde, reverse=True):
        nuevas = ed.nuevo.split("\n")  # el 'nuevo' viene con \n lógicos
        lineas[ed.desde - 1: ed.hasta] = nuevas
    return eol.join(lineas)


def editar(lugar: Lugar, ruta: str,
           literales: list[EdicionLiteral] | None = None,
           lineas: list[EdicionLinea] | None = None,
           ancladas: list[EdicionAnclada] | None = None) -> str:
    """
    Aplica ediciones a un archivo con validación en dos fases:
      1) Lee, detecta EOL, y valida TODAS las ediciones en memoria. Si algo
         falla, no escribe nada.
      2) Hace backup y escribe el resultado.
    Se pueden combinar literales, por-línea y ancladas en la misma llamada.
    Devuelve un resumen + el fragmento resultante alrededor de la edición.
    """
    literales = literales or []
    lineas = lineas or []
    ancladas = ancladas or []
    if not literales and not lineas and not ancladas:
        raise EdicionError("No se especificó ninguna edición.")

    data = _leer_bytes(lugar, ruta)
    texto = _decodificar(data)
    eol = _detectar_eol(texto)

    # Normalizar a LF SOLO si el archivo usa CRLF: así editar_literal compara
    # contra texto en LF y no falla cuando el archivo tiene CRLF pero el 'viejo'
    # viene en LF (la fricción nº1). Si el archivo ya está en LF (Linux y muchos
    # de Windows) no se toca: cero trabajo extra. La Fase 2 re-aplica el EOL
    # original al escribir, así que los CRLF del archivo se preservan igual.
    if eol == "\r\n":
        texto = texto.replace("\r\n", "\n")

    # Fase 1: validar y construir el resultado en memoria.
    resultado = texto
    for ed in literales:
        resultado = _aplicar_literal(resultado, ed)
    if ancladas:
        # Trabajamos en LF: las funciones internas splitean por "\n", no por el
        # EOL original (que solo se re-aplica al escribir, en la Fase 2).
        resultado = _aplicar_ancladas(resultado, ancladas, "\n")
    if lineas:
        resultado = _aplicar_lineas(resultado, lineas, "\n")

    # Fase 2: backup + escritura, preservando el EOL original.
    bak = _backup(lugar, ruta, data)
    resultado_norm = resultado.replace("\r\n", "\n").replace("\n", eol)
    _escribir_bytes(lugar, ruta, resultado_norm.encode("utf-8"))

    # Contexto post-edición: si hubo edición por rango, mostrar ese tramo
    # (± 2 líneas) ya editado, para verificar en el acto sin un leer_rango aparte.
    extracto = ""
    rangos = [(e.desde, e.hasta) for e in lineas] + \
             [(e.desde, e.hasta) for e in ancladas]
    if rangos:
        nuevas_lineas = resultado_norm.split(eol)
        ini = max(1, min(d for d, _ in rangos) - 2)
        fin = min(len(nuevas_lineas), max(h for _, h in rangos) + 2)
        cuerpo = "\n".join(
            f"{n}\t{nuevas_lineas[n - 1]}" for n in range(ini, fin + 1)
        )
        extracto = f"\n--- resultado ({ini}-{fin}) ---\n{cuerpo}"

    return (
        f"Editado {ruta} en {lugar.nombre} "
        f"({len(literales)} literal(es), {len(lineas)} por-línea, "
        f"{len(ancladas)} anclada(s)). "
        f"EOL preservado: {'CRLF' if eol == chr(13)+chr(10) else 'LF'}. "
        f"Backup: {bak}{extracto}"
    )


def convertir_eol(lugar: Lugar, ruta: str, a: str) -> str:
    """
    Convierte el fin de línea de un archivo entero a LF o CRLF.
    'a' debe ser "lf" o "crlf". Hace backup antes. Reescribe todo el archivo,
    así que en git aparecerá como muchas líneas cambiadas (es lo esperado al
    convertir). Para ediciones normales NO se usa esto: las tools de edición
    preservan el EOL existente.
    """
    objetivo = a.strip().lower()
    if objetivo not in ("lf", "crlf"):
        raise EdicionError("El parámetro 'a' debe ser 'lf' o 'crlf'.")
    nuevo_eol = "\n" if objetivo == "lf" else "\r\n"

    data = _leer_bytes(lugar, ruta)
    texto = _decodificar(data)
    eol_actual = _detectar_eol(texto)

    # Normalizar a LF y luego aplicar el objetivo (maneja archivos mezclados).
    lf = texto.replace("\r\n", "\n")
    resultado = lf.replace("\n", nuevo_eol) if objetivo == "crlf" else lf

    if resultado.encode("utf-8") == data:
        return (f"{ruta} ya estaba en {objetivo.upper()}; no se cambió nada.")

    bak = _backup(lugar, ruta, data)
    _escribir_bytes(lugar, ruta, resultado.encode("utf-8"))
    antes = "CRLF" if eol_actual == "\r\n" else "LF"
    return (f"Convertido {ruta} en {lugar.nombre}: {antes} -> {objetivo.upper()}. "
            f"Backup: {bak}")


# --- Listado / info / carpetas ---------------------------------------------

def listar(lugar: Lugar, ruta: str) -> str:
    if lugar.es_local:
        p = normalizar(lugar.raiz, ruta)
        items = []
        for hijo in sorted(p.iterdir()):
            tipo = "DIR " if hijo.is_dir() else "FILE"
            items.append(f"[{tipo}] {hijo.name}")
        return "\n".join(items) if items else "(vacío)"
    r = T.ejecutar(lugar, ["ls", "-la", ruta])
    return r.salida if r.ok else f"error: {r.error}"


def crear_carpeta(lugar: Lugar, ruta: str) -> str:
    if lugar.es_local:
        p = normalizar(lugar.raiz, ruta)
        p.mkdir(parents=True, exist_ok=True)
        return f"Carpeta creada: {p}"
    r = T.ejecutar(lugar, ["mkdir", "-p", ruta])
    return "ok" if r.ok else f"error: {r.error}"


def mover(lugar: Lugar, origen: str, destino: str) -> str:
    """Mover/renombrar DENTRO de un mismo lugar."""
    if lugar.es_local:
        po = normalizar(lugar.raiz, origen)
        pd = normalizar(lugar.raiz, destino)
        pd.parent.mkdir(parents=True, exist_ok=True)
        po.rename(pd)
        return f"Movido {po} -> {pd}"
    r = T.ejecutar(lugar, ["mv", origen, destino])
    return "ok" if r.ok else f"error: {r.error}"


def borrar(lugar: Lugar, ruta: str) -> str:
    """
    Borra un archivo o carpeta moviéndolo a una papelera dentro de .witral,
    con timestamp (recuperable). No hace borrado definitivo. La carpeta se mueve
    con todo su contenido. Acotado a la raíz del lugar.
    """
    ts = time.strftime("%Y%m%d-%H%M%S")
    if lugar.es_local:
        import shutil
        p = normalizar(lugar.raiz, ruta)
        if not p.exists():
            raise FileNotFoundError(f"No existe: {ruta}")
        papelera = Path(lugar.raiz) / ".witral" / "papelera"
        papelera.mkdir(parents=True, exist_ok=True)
        destino = papelera / f"{p.name}.{ts}"
        shutil.move(str(p), str(destino))
        tipo = "carpeta" if destino.is_dir() else "archivo"
        return f"Borrado ({tipo}) {ruta} -> papelera: {destino}"
    # remoto: mover a ~/.witral/papelera del lugar (relativo al home remoto)
    papelera = ".witral/papelera"
    nombre = ruta.rstrip("/").split("/")[-1]
    destino = f"{papelera}/{nombre}.{ts}"
    cmd = f"mkdir -p {papelera} && mv {_q(ruta)} {_q(destino)}"
    r = T.ejecutar(lugar, cmd)
    return f"Borrado {ruta} -> papelera: {destino}" if r.ok else f"error: {r.error}"


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def vaciar_papelera(lugar: Lugar) -> str:
    """Borra DEFINITIVAMENTE el contenido de la papelera del lugar."""
    if lugar.es_local:
        import shutil
        papelera = Path(lugar.raiz) / ".witral" / "papelera"
        if not papelera.exists():
            return "La papelera ya está vacía."
        n = sum(1 for _ in papelera.iterdir())
        shutil.rmtree(papelera)
        papelera.mkdir(parents=True, exist_ok=True)
        return f"Papelera vaciada definitivamente ({n} elemento(s))."
    r = T.ejecutar(lugar, "rm -rf .witral/papelera/* 2>/dev/null; echo ok")
    return "Papelera vaciada." if r.ok else f"error: {r.error}"
