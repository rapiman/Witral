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

    En unix/remoto compila síncrono y devuelve la salida. En local Windows NO
    puede compilar: el build necesita sockets loopback que el sandbox del cliente
    MCP bloquea (ver Notas técnicas del README). Devuelve un aviso para correr el
    build en una terminal propia.
    """
    if lugar.es_local:
        p = normalizar(lugar.raiz, proyecto)
        if lugar.es_windows:
            return (
                "No puedo compilar desde acá: el sandbox del cliente MCP bloquea "
                "los sockets loopback que Gradle/Java necesitan. Corré el build en "
                "tu terminal:\n"
                f'    cd "{p}"\n'
                f"    .\\gradlew {tarea}\n"
                "Una vez generado el APK, puedo desplegarlo con adb_install."
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
