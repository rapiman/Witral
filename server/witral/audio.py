"""
Audio: transcripción con faster-whisper (ronda 19).

La transcripción en sí vive en transcribir.py, un script suelto; aquí solo se
arma el comando y se lanza como TRABAJO (trabajos.lanzar), porque un audio de
minutos tarda minutos en CPU.

- local: corre con el python del venv de Witral (sys.executable), que tiene
  faster-whisper si se instaló el extra: `uv sync --extra audio --inexact`.
- remoto (unix): el script se lleva a ~/.witral/transcribir.py y corre con
  python3 de allá; si ahí falta faster-whisper, el trabajo termina con
  código 3 y el error lo dice.
"""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

from .config import Lugar
from .seguridad import normalizar
from . import trabajos as TR
from . import transporte as T

_SCRIPT = Path(__file__).with_name("transcribir.py")


def transcribir(lugar: Lugar, archivo: str, modelo: str = "medium",
                idioma: str = "es", salida: str = "", vad: bool = True,
                prompt: str = "", umbral: float = 0.5) -> str:
    args = ["--modelo", modelo, "--idioma", idioma or "auto",
            "--vad", "si" if vad else "no", "--umbral", str(umbral)]
    if prompt:
        args += ["--prompt", prompt]

    if lugar.es_local:
        p = normalizar(lugar.raiz, archivo)
        if not p.is_file():
            return f"error: no existe el audio {p}"
        if salida:
            args += ["--salida", str(normalizar(lugar.raiz, salida))]
        partes = [sys.executable, str(_SCRIPT), str(p)] + args
        if lugar.es_windows:
            # El comando va a un .cmd: ahí % se expande aunque esté entre comillas.
            if "%" in prompt or '"' in prompt:
                return "error: el prompt no puede llevar % ni comillas dobles (van a un .cmd)"
            cmd = subprocess.list2cmdline(partes)
        else:
            cmd = " ".join(T.comillas(x) for x in partes)
        destino = Path(args[args.index("--salida") + 1]) if salida else p.parent
    else:
        src = base64.b64encode(_SCRIPT.read_bytes()).decode("ascii")
        r = T.ejecutar(lugar, f"mkdir -p .witral && printf %s {src} | base64 -d "
                              f"> .witral/transcribir.py", timeout=30)
        if not r.ok:
            return f"error: no se pudo llevar el script a {lugar.nombre}: {r.error or r.salida}"
        if salida:
            args += ["--salida", salida]
        cmd = ('python3 "$HOME/.witral/transcribir.py" '
               + " ".join(T.comillas(x) for x in [archivo] + args))
        destino = salida or f"(carpeta del audio en {lugar.nombre})"

    jid = TR.lanzar(lugar, cmd)
    nombre = Path(archivo).stem
    return (f"Transcripción lanzada: id {jid} en {lugar.nombre} (modelo {modelo}, "
            f"idioma {idioma or 'auto'}).\n"
            f"Seguirla con run_esperar(id=\"{jid}\", donde=\"{lugar.nombre}\"): el "
            f"progreso sale segmento a segmento. La PRIMERA vez con cada modelo se "
            f"descarga (medium ~1,5 GB; large-v3 ~3 GB).\n"
            f"Salidas en {destino}: {nombre}.txt, {nombre}.tiempos.txt, "
            f"{nombre}.srt, {nombre}.lrc")
