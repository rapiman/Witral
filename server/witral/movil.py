"""
ADB y Gradle, acotados por parámetros (no línea de comando libre).

ADB tiene dos coordenadas de "dónde": `donde` (qué máquina corre el binario adb)
y `serial` (qué dispositivo de esa máquina). Gradle invoca el `gradlew` del
proyecto.
"""

from __future__ import annotations

import os

from .config import Lugar
from .seguridad import normalizar
from . import transporte as T


# --- ADB --------------------------------------------------------------------

def adb_devices(lugar: Lugar) -> T.Resultado:
    return T.ejecutar(lugar, ["adb", "devices", "-l"])


def adb_shell(lugar: Lugar, serial: str, comando: str) -> T.Resultado:
    """
    Ejecuta `adb -s <serial> shell <comando>`. Acotado: siempre invoca adb shell;
    'comando' es lo que corre dentro del shell del dispositivo.
    """
    return T.ejecutar(lugar, ["adb", "-s", serial, "shell", comando])


def adb_install(lugar: Lugar, serial: str, apk: str, reemplazar: bool = True) -> T.Resultado:
    args = ["adb", "-s", serial, "install"]
    if reemplazar:
        args.append("-r")
    args.append(apk)
    return T.ejecutar(lugar, args, timeout=300)


def adb_forcestop(lugar: Lugar, serial: str, paquete: str) -> T.Resultado:
    return T.ejecutar(lugar, ["adb", "-s", serial, "shell", "am", "force-stop", paquete])


def adb_relanzar(lugar: Lugar, serial: str, paquete: str) -> T.Resultado:
    """force-stop seguido de monkey -p para relanzar la app."""
    return T.ejecutar(
        lugar,
        ["adb", "-s", serial, "shell", "monkey", "-p", paquete,
         "-c", "android.intent.category.LAUNCHER", "1"],
    )


# --- Gradle -----------------------------------------------------------------

def gradle_build(lugar: Lugar, proyecto: str, tarea: str) -> str:
    """
    Compila con el gradlew del proyecto.

    En local Windows el build necesita sockets loopback que el sandbox del
    cliente MCP bloquea: se lanza como tarea programada (async) y se devuelve el
    nombre de la tarea para seguirla con tarea_estado. En unix/remoto compila
    síncrono y devuelve la salida.
    """
    if lugar.es_local:
        p = normalizar(lugar.raiz, proyecto)
        if lugar.es_windows:
            # 'call' es necesario: sin él, cmd transfiere el control a
            # gradlew.bat y no regresa al script, perdiendo la captura de salida.
            comando = f'call "{p / "gradlew.bat"}" -p "{p}" {tarea}'
            nombre = T.tarea_lanzar(comando, cwd=str(p))
            return (
                f"Build lanzado como tarea '{nombre}' (fuera del sandbox).\n"
                f"Seguí el avance con tarea_estado('{nombre}'). "
                f"Un build limpio puede tardar varios minutos."
            )
        salida = T.ejecutar(lugar, ["./gradlew", tarea], cwd=str(p), timeout=1800)
        return _fmt_resultado(salida)
    salida = T.ejecutar(lugar, f"cd '{proyecto}' && ./gradlew {tarea}", timeout=1800)
    return _fmt_resultado(salida)


def _fmt_resultado(r: T.Resultado) -> str:
    cuerpo = (r.salida or "").rstrip()
    if r.error:
        cuerpo += ("\n--- stderr ---\n" + r.error.rstrip())
    return f"[código {r.codigo}]\n{cuerpo}".rstrip()
