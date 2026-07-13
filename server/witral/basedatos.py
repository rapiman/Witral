"""
Base de datos = correr el cliente nativo (psql) en el lugar, donde la base es
local para ese lugar. No se exponen puertos ni se usan drivers Python.

Distingue lectura de escritura: las sentencias destructivas requieren que la
capa de tools haya confirmado con el usuario (el parámetro `confirmado`). En
lugares marcados como sensibles (prod), la confirmación es obligatoria incluso
para cosas que en dev pasarían — esa decisión la toma la capa superior; aquí
solo se expone la señal `destructivo` para que la tool actúe en consecuencia.
"""

from __future__ import annotations

import re

from .config import Lugar, DBConfig
from . import transporte as T


# Palabras que indican modificación de datos o esquema.
_DESTRUCTIVO = re.compile(
    r"\b(update|delete|drop|truncate|alter|insert|create|grant|revoke)\b",
    re.IGNORECASE,
)


def es_destructivo(sql: str) -> bool:
    """Heurística: ¿el SQL modifica datos o esquema?"""
    # Quitar comentarios de línea para no confundir.
    limpio = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    return bool(_DESTRUCTIVO.search(limpio))


def partir_sentencias(sql: str) -> list[str]:
    """
    Parte un bloque SQL en sentencias por ';' de tope, respetando strings
    ('...'  y "..."), comentarios (-- de línea y /* */ de bloque) y
    dollar-quoting ($tag$...$tag$). Devuelve las sentencias con texto (sin las
    vacías); el ';' NO se incluye. Es una heurística suficiente para separar
    lecturas de escrituras, no un parser completo de SQL.
    """
    sentencias: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        par = sql[i:i + 2]
        # Comentario de línea.
        if par == "--":
            j = sql.find("\n", i)
            j = n if j == -1 else j + 1
            buf.append(sql[i:j])
            i = j
            continue
        # Comentario de bloque.
        if par == "/*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            buf.append(sql[i:j])
            i = j
            continue
        # Dollar-quoting: $tag$ ... $tag$ (tag alfanumérico o vacío).
        if c == "$":
            m = re.match(r"\$[A-Za-z0-9_]*\$", sql[i:])
            if m:
                etiqueta = m.group(0)
                fin = sql.find(etiqueta, i + len(etiqueta))
                fin = n if fin == -1 else fin + len(etiqueta)
                buf.append(sql[i:fin])
                i = fin
                continue
        # Strings con comilla simple o doble (dobla-comilla escapa).
        if c in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            buf.append(sql[i:j])
            i = j
            continue
        if c == ";":
            texto = "".join(buf).strip()
            if texto:
                sentencias.append(texto)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    resto = "".join(buf).strip()
    if resto:
        sentencias.append(resto)
    return sentencias


def separar_lectura_escritura(sql: str) -> tuple[list[str], list[str]]:
    """
    Separa un bloque en (lecturas, escrituras) sentencia a sentencia, con la
    MISMA heurística es_destructivo por sentencia. Una sentencia que no matchea
    ninguna palabra destructiva es lectura; el resto, escritura. Permite correr
    las lecturas sin confirmación y pedirla solo por las escrituras.
    """
    lecturas, escrituras = [], []
    for s in partir_sentencias(sql):
        (escrituras if es_destructivo(s) else lecturas).append(s)
    return lecturas, escrituras


def _fallo_conexion(err: str) -> bool:
    """¿El stderr de psql delata una caída de CONEXIÓN (no un error de SQL)?"""
    e = (err or "").lower()
    señales = (
        "10054", "could not receive data from server",
        "server closed the connection unexpectedly",
        "could not connect", "connection reset", "connection refused",
        "no connection to the server", "terminating connection",
    )
    return any(s in e for s in señales)


def _base_args(db: DBConfig) -> list[str]:
    args = [db.cliente]
    # Modo peer (db.como): conexión por socket local como usuario del sistema.
    # No se pasan -h/-U: psql usa el socket Unix y el rol del usuario del SO.
    if not db.como:
        if db.host:
            args += ["-h", db.host]
        if db.puerto:
            args += ["-p", str(db.puerto)]
        if db.usuario:
            args += ["-U", db.usuario]
    if db.base:
        args += ["-d", db.base]
    # Salida limpia para el modelo.
    args += ["-v", "ON_ERROR_STOP=1", "--no-psqlrc"]
    # -w (--no-password): nunca pedir password interactivo. Si la base lo exige y
    # no hay credencial, psql falla AL INSTANTE en vez de colgarse esperando un
    # prompt que nadie va a responder (el cuelgue de 4 minutos).
    args += ["-w"]
    return args


def _con_base(db: DBConfig, base: str | None) -> DBConfig:
    """Copia de la config de base con la base override, si se pidió otra."""
    if not base or base == db.base:
        return db
    import dataclasses
    return dataclasses.replace(db, base=base)


def psql_comando(lugar: Lugar, comando: str, base: str | None = None) -> T.Resultado:
    """
    Ejecuta SQL/meta-comandos vía psql con el SQL por STDIN (no -c): con
    varias sentencias en una llamada psql imprime TODOS los result sets,
    no solo el último (el modo -c ocultaba los anteriores).
    'base' permite apuntar a otra base del mismo lugar sin tocar config.
    """
    db = _con_base(lugar.requiere_db(), base)
    args = _base_args(db)
    entrada = comando if comando.endswith("\n") else comando + "\n"
    # Solo reintentar en caída de conexión si NO es destructivo: reintentar una
    # escritura podría duplicar efectos. Las lecturas se reintentan sin riesgo.
    return _correr(lugar, db, args, entrada=entrada,
                   reintentable=not es_destructivo(comando))


def psql_archivo(lugar: Lugar, ruta_sql: str, origen: Lugar | None = None,
                 base: str | None = None) -> T.Resultado:
    """
    Aplica un archivo .sql: Witral LEE el archivo (con sus tools de archivo,
    desde 'origen' — por defecto el mismo lugar de la base) y manda el
    contenido por STDIN al psql del lugar de la BASE. Así se desacopla
    "dónde está el .sql" de "dónde corre psql": sirve para bases detrás de
    túnel (el psql no ve el filesystem local) y evita el boilerplate psycopg.
    'base' permite apuntar a otra base del mismo lugar.
    """
    from . import archivos as A
    db = _con_base(lugar.requiere_db(), base)
    org = origen if origen is not None else lugar
    contenido = A._leer_bytes(org, ruta_sql).decode("utf-8-sig", "replace")
    if not contenido.strip():
        return T.Resultado(1, "", f"el archivo {ruta_sql} está vacío")
    if not contenido.endswith("\n"):
        contenido += "\n"
    args = _base_args(db)
    return _correr(lugar, db, args, entrada=contenido)


def _correr(lugar: Lugar, db: DBConfig, args: list[str],
            entrada: str | None = None, reintentable: bool = False) -> T.Resultado:
    # Entorno común: timeout de conexión corto (no colgarse si la base no
    # responde) y salida en UTF-8 (evita mojibake al decodificar).
    env_extra = {"PGCONNECT_TIMEOUT": "10", "PGCLIENTENCODING": "UTF8"}

    def _una_vez() -> T.Resultado:
        if lugar.es_local:
            e2 = dict(env_extra)
            if db.password:
                e2["PGPASSWORD"] = db.password
            # Con -w en los args, si la base pide password y no hay credencial,
            # psql falla al instante en vez de esperar un prompt.
            return T.ejecutar(lugar, args, entrada=entrada, timeout=60,
                              env_extra=e2)
        linea = " ".join(_q(a) for a in args)
        prefijo = " ".join(f"{k}={_q(v)}" for k, v in env_extra.items())
        if db.como:
            # Peer auth: ejecutar como el usuario del sistema vía sudo. Sin
            # password TCP; psql usa el socket local con el rol de ese usuario.
            # 'env' para que las variables lleguen al proceso bajo sudo.
            linea = f"sudo -u {_q(db.como)} env {prefijo} {linea}"
        else:
            pref = prefijo
            if db.password:
                pref = f"PGPASSWORD={_q(db.password)} {prefijo}"
            linea = f"{pref} {linea}"
        return T.ejecutar(lugar, linea, entrada=entrada, timeout=60)

    r = _una_vez()
    # Reintento único ante caída de CONEXIÓN (no error de SQL). Solo cuando el
    # llamador marcó la operación como segura de repetir (lectura): absorbe el
    # WinError 10054 / "server closed the connection" transitorio sin arriesgar
    # duplicar una escritura.
    if reintentable and r.codigo != 0 and _fallo_conexion(r.error):
        import time as _t
        _t.sleep(1.0)
        r = _una_vez()
    return r


_q = T.comillas  # comilla POSIX: origen único en transporte.comillas
