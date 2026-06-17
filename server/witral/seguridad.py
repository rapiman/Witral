"""
Acotamiento de rutas. Toda operación de archivo en el lugar local debe quedar
dentro de la raíz autorizada del lugar. En remoto, dentro de las rutas del
lugar (validación análoga, aplicada del lado del comando remoto).

La función central es `dentro_de`, que resuelve la ruta de forma segura
(sin permitir escapar con '..' ni symlinks que salgan de la raíz) y lanza
RutaFueraDeRaiz si el destino cae afuera.
"""

from __future__ import annotations

import os
from pathlib import Path


class RutaFueraDeRaiz(Exception):
    def __init__(self, ruta: str, raiz: str):
        self.ruta = ruta
        self.raiz = raiz
        super().__init__(
            f"Ruta fuera de la raíz autorizada.\n  ruta: {ruta}\n  raíz: {raiz}"
        )


def normalizar(raiz: str, ruta: str) -> Path:
    """
    Resuelve 'ruta' relativa a 'raiz' (si es relativa) o tal cual (si es
    absoluta), y verifica que el resultado quede dentro de 'raiz'. Devuelve
    la Path resuelta. No requiere que el archivo exista.
    """
    base = Path(raiz).resolve()
    p = Path(ruta)
    if not p.is_absolute():
        p = base / p
    # resolve() colapsa '..' y symlinks. strict=False: no exige existencia.
    resuelta = p.resolve()
    try:
        resuelta.relative_to(base)
    except ValueError:
        raise RutaFueraDeRaiz(str(resuelta), str(base))
    return resuelta


def es_subruta(raiz: str, ruta: str) -> bool:
    try:
        normalizar(raiz, ruta)
        return True
    except RutaFueraDeRaiz:
        return False
