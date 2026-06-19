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


def _entorno(db: DBConfig) -> dict:
    """Entorno para psql: timeout de conexión corto siempre, y PGPASSWORD si hay."""
    import os
    env = dict(os.environ)
    # PGCONNECT_TIMEOUT: si el host no responde, abortar en ~10s en vez de colgarse.
    env.setdefault("PGCONNECT_TIMEOUT", "10")
    if db.password:
        env["PGPASSWORD"] = db.password
    return env


def psql_comando(lugar: Lugar, comando: str) -> T.Resultado:
    """
    Ejecuta un comando/consulta vía `psql -c`. Para SQL ad-hoc y meta-comandos.
    """
    db = lugar.requiere_db()
    args = _base_args(db) + ["-c", comando]
    return _correr(lugar, db, args)


def psql_archivo(lugar: Lugar, ruta_sql: str) -> T.Resultado:
    """
    Aplica un archivo .sql vía `psql -f`. La ruta es del lado del lugar
    (el .sql ya llegó allí por git o copiar). Caso central de migraciones.
    """
    db = lugar.requiere_db()
    # En local, normalizar la ruta como las tools de archivo (relativa contra la
    # raíz o absoluta, acotada a la raíz), para que psql no la interprete desde
    # su propio directorio. En remoto la ruta es del lado del lugar, tal cual.
    if lugar.es_local:
        from .seguridad import normalizar
        ruta_sql = str(normalizar(lugar.raiz, ruta_sql))
    args = _base_args(db) + ["-f", ruta_sql]
    return _correr(lugar, db, args)


def _correr(lugar: Lugar, db: DBConfig, args: list[str]) -> T.Resultado:
    if lugar.es_local:
        # Entorno con PGCONNECT_TIMEOUT (y PGPASSWORD si hay). Con -w en los args,
        # si la base pide password y no hay credencial, psql falla al instante.
        env = _entorno(db)
        import subprocess
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=60, env=env,
                input="",
            )
            return T.Resultado(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as e:
            return T.Resultado(124, e.stdout or "",
                               "timeout (60s): la base no respondió a tiempo")
    else:
        # En remoto, prefijar PGPASSWORD en la línea si hay password.
        linea = " ".join(_q(a) for a in args)
        if db.password:
            linea = f"PGPASSWORD={_q(db.password)} {linea}"
        return T.ejecutar(lugar, linea, timeout=60)


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
