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
    return _correr(lugar, db, args, entrada=entrada)


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
            entrada: str | None = None) -> T.Resultado:
    # Entorno común: timeout de conexión corto (no colgarse si la base no
    # responde) y salida en UTF-8 (evita mojibake al decodificar).
    env_extra = {"PGCONNECT_TIMEOUT": "10", "PGCLIENTENCODING": "UTF8"}
    if lugar.es_local:
        if db.password:
            env_extra["PGPASSWORD"] = db.password
        # Con -w en los args, si la base pide password y no hay credencial,
        # psql falla al instante en vez de esperar un prompt.
        return T.ejecutar(lugar, args, entrada=entrada, timeout=60,
                          env_extra=env_extra)
    else:
        linea = " ".join(_q(a) for a in args)
        prefijo = " ".join(f"{k}={_q(v)}" for k, v in env_extra.items())
        if db.como:
            # Peer auth: ejecutar como el usuario del sistema vía sudo. Sin
            # password TCP; psql usa el socket local con el rol de ese usuario.
            # 'env' para que las variables lleguen al proceso bajo sudo.
            linea = f"sudo -u {_q(db.como)} env {prefijo} {linea}"
        else:
            if db.password:
                prefijo = f"PGPASSWORD={_q(db.password)} {prefijo}"
            linea = f"{prefijo} {linea}"
        return T.ejecutar(lugar, linea, entrada=entrada, timeout=60)


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
