"""
Mover cosas entre lugares es una acción. `copiar` tiende el puente entre dos
lugares (origen y destino), en cualquier sentido, vía SFTP.

Casos:
  - local  -> remoto : subir (un .sql, web, artefacto)
  - remoto -> local  : bajar
  - local  -> local  : copia de archivo en disco
  - remoto -> remoto : baja a un temporal local y sube al otro (passthrough)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .config import Config, Lugar
from .seguridad import normalizar
from . import transporte as T


def partir_lugar_ruta(spec: str, nombres, default_lugar: str = "local"):
    """
    Parsea la forma compacta 'lugar:ruta' -> (lugar, ruta).

    El prefijo antes del PRIMER ':' se toma como lugar SOLO si es un lugar
    conocido (está en 'nombres'). Si no lo es —una ruta Windows 'C:\\...', una
    ruta unix '/srv/...' sin prefijo, o cualquier ':' que no sea separador de
    lugar— se devuelve (default_lugar, spec) sin tocar. Así la sintaxis compacta
    convive con las rutas absolutas sin ambigüedad.
    """
    if ":" in spec:
        pre, resto = spec.split(":", 1)
        if pre in nombres:
            return pre, resto
    return default_lugar, spec


def copiar(cfg: Config, origen_lugar: str | None, origen_ruta: str,
           destino_lugar: str | None, destino_ruta: str) -> str:
    o = cfg.resolver(origen_lugar)
    d = cfg.resolver(destino_lugar)

    if o.es_local and d.es_local:
        po = normalizar(o.raiz, origen_ruta)
        pd = normalizar(d.raiz, destino_ruta)
        pd.parent.mkdir(parents=True, exist_ok=True)
        pd.write_bytes(po.read_bytes())
        return f"Copiado (local→local) {po} -> {pd}"

    if o.es_local and not d.es_local:
        po = normalizar(o.raiz, origen_ruta)
        T.subir(d, str(po), destino_ruta)
        return f"Copiado (local→{d.nombre}) {origen_ruta} -> {destino_ruta}"

    if not o.es_local and d.es_local:
        pd = normalizar(d.raiz, destino_ruta)
        pd.parent.mkdir(parents=True, exist_ok=True)
        T.bajar(o, origen_ruta, str(pd))
        return f"Copiado ({o.nombre}→local) {origen_ruta} -> {destino_ruta}"

    # remoto -> remoto: passthrough por temporal local.
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
    try:
        T.bajar(o, origen_ruta, tmp_path)
        T.subir(d, tmp_path, destino_ruta)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return f"Copiado ({o.nombre}→{d.nombre}) {origen_ruta} -> {destino_ruta}"
