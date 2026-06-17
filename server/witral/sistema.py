"""
Operaciones de sistema con el eje `donde`, ramificando por el SO del lugar
(windows vs unix). La misma tool produce el comando correcto según `lugar.so`,
así quien la usa no piensa en taskkill vs pkill ni en sc vs systemctl.

Todo corre vía el transporte del lugar (subprocess local o SSH remoto).
"""

from __future__ import annotations

from .config import Lugar
from . import transporte as T


# --- Procesos ---------------------------------------------------------------

def procesos(lugar: Lugar, filtro: str = "") -> T.Resultado:
    """Lista procesos. Opcionalmente filtra por nombre/patrón."""
    if lugar.es_windows:
        cmd = "tasklist"
        if filtro:
            cmd += f' | findstr /I "{filtro}"'
    else:
        cmd = "ps aux"
        if filtro:
            cmd += f" | grep -i {_q(filtro)} | grep -v grep"
    return T.ejecutar(lugar, cmd)


def matar_proceso(lugar: Lugar, patron: str) -> T.Resultado:
    """Mata procesos cuyo nombre/línea coincide con 'patron'."""
    if lugar.es_windows:
        # Por nombre de imagen (ej. "node.exe"); /F fuerza, /T incluye hijos.
        cmd = f'taskkill /F /T /IM "{patron}"'
    else:
        cmd = f"pkill -f {_q(patron)}"
    return T.ejecutar(lugar, cmd)


# --- Servicios --------------------------------------------------------------

_ACCIONES = {"status", "start", "stop", "restart"}


def servicio(lugar: Lugar, accion: str, nombre: str) -> T.Resultado:
    """Controla un servicio: status | start | stop | restart."""
    accion = accion.lower()
    if accion not in _ACCIONES:
        return T.Resultado(2, "", f"acción inválida: {accion}. Usá: {', '.join(sorted(_ACCIONES))}")
    if lugar.es_windows:
        if accion == "restart":
            r1 = T.ejecutar(lugar, f'sc stop "{nombre}"')
            r2 = T.ejecutar(lugar, f'sc start "{nombre}"')
            salida = (r1.salida + "\n" + r2.salida).strip()
            return T.Resultado(r2.codigo, salida, (r1.error + r2.error).strip())
        mapa = {"status": "query", "start": "start", "stop": "stop"}
        cmd = f'sc {mapa[accion]} "{nombre}"'
    else:
        cmd = f"systemctl {accion} {_q(nombre)}"
    return T.ejecutar(lugar, cmd)


# --- Helpers ----------------------------------------------------------------

def _q(s: str) -> str:
    """Comilla simple segura para shell unix."""
    return "'" + s.replace("'", "'\\''") + "'"
