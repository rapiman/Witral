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


def _rsync_args(origen: str, destino: str, excluir: list[str],
                borrar: bool, seco: bool) -> list[str]:
    """
    Arma el rsync. La barra final en el ORIGEN es la diferencia entre copiar el
    contenido del directorio y copiar el directorio adentro del destino: se
    fuerza siempre, porque la variante sin barra casi nunca es la que se quiere
    y el error se descubre tarde.
    """
    o = origen if origen.endswith("/") else origen + "/"
    args = ["rsync", "-a", "--itemize-changes"]
    if borrar:
        args.append("--delete")
    if seco:
        args.append("--dry-run")
    for patron in excluir:
        patron = patron.strip()
        if patron:
            args += ["--exclude", patron]
    args += [o, destino]
    return args


def _lineas_borrado(salida: str) -> list[str]:
    """Las líneas de rsync que corresponden a un BORRADO en el destino."""
    return [l for l in salida.splitlines() if l.startswith("*deleting")]


def sincronizar(cfg: Config, origen: str, destino: str, excluir: list[str],
                borrar: bool, seco: bool, confirmado: bool) -> str:
    """
    Sincroniza un ÁRBOL entre dos rutas del MISMO lugar unix, con rsync -a.

    Por qué existe: `desplegar` cubre un archivo, pero la operación repetida de
    un sitio es sincronizar el repo al webroot con varios excludes, y esa se
    escribía a mano por `run` en cada sesión. Es justo la clase de comando donde
    un exclude mal tipeado, sumado a --delete, borra una carpeta de uploads sin
    preguntar.

    Por eso el borrado NO se ejecuta a ciegas: con borrar=True y sin
    confirmado=True se corre un ENSAYO (--dry-run) y se devuelve la lista exacta
    de lo que se borraría, para decidir sobre hechos y no sobre la lectura del
    comando. seco=True fuerza el ensayo aunque haya confirmación.
    """
    o_lugar, o_ruta = partir_lugar_ruta(origen, cfg.nombres)
    d_lugar, d_ruta = partir_lugar_ruta(destino, cfg.nombres)
    o = cfg.resolver(o_lugar)
    d = cfg.resolver(d_lugar)
    if o.nombre != d.nombre:
        return (f"sincronizar opera DENTRO de un mismo lugar (rsync local a esa "
                f"máquina); acá vienen '{o.nombre}' y '{d.nombre}'. Para mover "
                f"entre lugares está `copiar` (SFTP, un archivo).")
    if o.es_windows:
        return (f"'{o.nombre}' es Windows: no hay rsync. Para un árbol en "
                f"Windows, robocopy por `run` (y su /MIR es el equivalente de "
                f"--delete: mismas precauciones).")

    ensayo = seco or (borrar and not confirmado)
    args = _rsync_args(o_ruta, d_ruta, excluir, borrar, ensayo)
    r = T.ejecutar(o, args, timeout=180)
    if r.codigo != 0:
        return (f"error de rsync (código {r.codigo}) en {o.nombre}:\n"
                f"{(r.error or r.salida).strip()}")

    borrados = _lineas_borrado(r.salida)
    cambios = [l for l in r.salida.splitlines() if l and l not in borrados]
    resumen = (f"{len(cambios)} archivo(s) a copiar/actualizar, "
               f"{len(borrados)} a borrar en el destino")

    if ensayo and borrar and not confirmado and not seco:
        detalle = "\n".join(f"  {l}" for l in borrados[:100]) or "  (ninguno)"
        if len(borrados) > 100:
            detalle += f"\n  ... y {len(borrados) - 100} más"
        return (f"ENSAYO (nada se tocó todavía). {resumen}.\n"
                f"SE BORRARÍA en {d_ruta}:\n{detalle}\n\n"
                f"Revisar esa lista: si aparece algo que no debería (uploads, "
                f"media, config del servidor), falta un patrón en 'excluir'. "
                f"Para ejecutar de verdad, reintentar con confirmado=True.")

    cabecera = "ENSAYO (nada se tocó)" if ensayo else "Sincronizado"
    detalle = "\n".join(f"  {l}" for l in r.salida.splitlines()[:200])
    if len(r.salida.splitlines()) > 200:
        detalle += f"\n  ... ({len(r.salida.splitlines())} líneas en total)"
    return (f"{cabecera}: {o_ruta} -> {d_ruta} en {o.nombre}. {resumen}.\n"
            f"{detalle}")


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
