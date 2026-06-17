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

def gradle_task(lugar: Lugar, proyecto: str, tarea: str) -> T.Resultado:
    """
    Corre una tarea de build con el gradlew del proyecto. El proyecto es la cwd.
    """
    if lugar.es_local:
        p = normalizar(lugar.raiz, proyecto)
        if os.name == "nt":
            return T.ejecutar(lugar, [str(p / "gradlew.bat"), tarea], cwd=str(p), timeout=1800)
        return T.ejecutar(lugar, ["./gradlew", tarea], cwd=str(p), timeout=1800)
    return T.ejecutar(lugar, f"cd '{proyecto}' && ./gradlew {tarea}", timeout=1800)
