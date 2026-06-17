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
    return args


def _entorno(db: DBConfig) -> dict | None:
    if db.password:
        import os
        env = dict(os.environ)
        env["PGPASSWORD"] = db.password
        return env
    return None


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
    args = _base_args(db) + ["-f", ruta_sql]
    return _correr(lugar, db, args)


def _correr(lugar: Lugar, db: DBConfig, args: list[str]) -> T.Resultado:
    if lugar.es_local:
        # En local podemos pasar PGPASSWORD por entorno de forma controlada.
        env = _entorno(db)
        if env is not None:
            import subprocess
            try:
                proc = subprocess.run(
                    args, capture_output=True, text=True, timeout=300, env=env
                )
                return T.Resultado(proc.returncode, proc.stdout, proc.stderr)
            except subprocess.TimeoutExpired as e:
                return T.Resultado(124, e.stdout or "", "timeout")
        return T.ejecutar(lugar, args, timeout=300)
    else:
        # En remoto, prefijar PGPASSWORD en la línea si hay password.
        linea = " ".join(_q(a) for a in args)
        if db.password:
            linea = f"PGPASSWORD={_q(db.password)} {linea}"
        return T.ejecutar(lugar, linea, timeout=300)


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
