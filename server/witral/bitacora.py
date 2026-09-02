"""
Bitácora de la escotilla: qué se ejecutó con run() y run_async(), cuándo y cómo
salió.

POR QUÉ EXISTE. `run` es de propósito general y se puede usar para cosas que ya
tienen tool tipada -- commitear, borrar, empujar. Eso no está prohibido, pero
hasta ahora no dejaba rastro: el único registro de un `git push --force` hecho
por acá era el historial de la conversación, que se pierde al cerrarla.

QUÉ NO HACE. No bloquea, no avisa, no cambia ningún comportamiento. Solo
registra. La idea es mirar el archivo después de unos días y ver con números
qué se está haciendo por la escotilla, para decidir qué tools faltan y qué
procesos conviene armar. Optimizar con datos, no de memoria.

DÓNDE ESCRIBE. Siempre en la máquina que corre el servidor: `.witral/cmd.log`
bajo la raíz del lugar local, aunque el comando haya sido para un lugar remoto.
Un solo archivo, con el lugar como columna; cruzar dos fuentes para reconstruir
una tarde de trabajo no sirve.

FORMATO. Una línea por invocación, campos separados por TAB, para que entre
igual de bien en `findstr` que en una planilla:

    fecha-hora  lugar  tool  conf  codigo  duracion_ms  comando

El comando va aplanado a una línea (los saltos como ⏎) y acotado, porque un
here-string de doscientas líneas haría el log ilegible sin aportar nada.

NUNCA REVIENTA. Si el registro falla -- disco lleno, permisos, la carpeta que
no está -- se traga el error y sigue. Una bitácora que voltea el comando que
venía a observar es peor que no tenerla.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

_ARCHIVO = "cmd.log"
_TOPE_COMANDO = 2000  # chars; lo que pase de ahí se corta con marca


def _aplanar(comando: str) -> str:
    """El comando en una sola línea, acotado, sin TABs que rompan las columnas."""
    plano = (comando or "").replace("\r\n", "\n").replace("\r", "\n")
    plano = plano.replace("\n", " ⏎ ").replace("\t", "    ").strip()
    if len(plano) > _TOPE_COMANDO:
        plano = plano[:_TOPE_COMANDO] + f" […+{len(plano) - _TOPE_COMANDO} chars]"
    return plano


def anotar(raiz_local: str | None, lugar: str, tool: str, comando: str,
           confirmado: bool, codigo: int | str = "", duracion_ms: int | str = "") -> None:
    """
    Agrega una línea a la bitácora. Silenciosa por diseño: cualquier problema al
    escribir se ignora, porque esto observa el trabajo, no lo condiciona.
    """
    if not raiz_local:
        return
    try:
        destino = Path(raiz_local) / ".witral" / _ARCHIVO
        destino.parent.mkdir(parents=True, exist_ok=True)
        cuando = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        linea = "\t".join([
            cuando,
            lugar or "?",
            tool,
            "conf" if confirmado else "-",
            str(codigo),
            str(duracion_ms),
            _aplanar(comando),
        ])
        with destino.open("a", encoding="utf-8", newline="\n") as f:
            f.write(linea + "\n")
    except Exception:
        pass
