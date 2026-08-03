"""
Runner de guiones de UI para el POS. Corre los pasos DEL LADO DEL DISPOSITIVO y
devuelve una sola línea cuando pasan; ante el primer fallo junta captura +
textos de pantalla + logcat. La economía: barato cuando pasa, caro solo cuando
falla (que es cuando gastar tokens vale la pena).

Verbos (uno por línea; '#' comenta; el primer token es el verbo):
  paquete <pkg>        directiva: app a controlar (o pasar 'paquete' a la tool)
  inicio               estado conocido: logcat -G 16M + -c, force-stop + relanzar
  tap <texto>          tap por texto/desc (espera a que aparezca; respeta lista negra)
  permitir <texto>     habilita un texto de la lista negra para el próximo tap
  esperar <texto>      espera a que el texto aparezca en la UI
  esperar_log <regex>  espera una línea de logcat que matchee (aserción determinista)
  verificar <texto>    asegura que el texto está presente (falla si no)
  no_debe <texto>      asegura que el texto NO está (falla si aparece)
  atras                keyevent BACK
  captura              screenshot opt-in, adjunto al resultado

Como el cliente MCP corta las llamadas largas (~45s), el runner corre con un
presupuesto de tiempo por llamada; si un guión largo no termina, devuelve
'EN PROGRESO: seguí con desde=K' y se retoma desde ese paso.
"""

from __future__ import annotations

import time

from .config import Lugar
from . import movil as M
from . import transporte as T


_VERBOS_ARG = {"paquete", "tap", "permitir", "esperar", "esperar_log",
               "verificar", "no_debe"}
_VERBOS_SOLO = {"inicio", "atras", "captura"}

_PRESUPUESTO = 38  # segundos por llamada (bajo el corte del cliente MCP ~45s)


def _nodos(lugar: Lugar, serial: str, intentos: int = 4) -> list:
    """Nodos del árbol reintentando ante volcados vacíos/transitorios
    ('null root node' mientras la app carga/anima): así verificar/no_debe no
    fallan en falso por un dump que llegó en mal momento."""
    nodos: list = []
    for _ in range(intentos):
        nodos, _est = M._ui_nodos_estable(lugar, serial)
        if nodos:
            return nodos
        time.sleep(0.5)
    return nodos


def parsear(texto: str):
    """(paquete_directiva, [(num, verbo, arg), ...]) o levanta ValueError."""
    paquete = None
    pasos = []
    for i, cruda in enumerate(texto.splitlines(), start=1):
        linea = cruda.split("#", 1)[0].strip()
        if not linea:
            continue
        partes = linea.split(None, 1)
        verbo = partes[0].lower()
        arg = partes[1].strip() if len(partes) > 1 else ""
        if verbo not in _VERBOS_ARG and verbo not in _VERBOS_SOLO:
            raise ValueError(f"línea {i}: verbo desconocido '{verbo}'")
        if verbo in _VERBOS_ARG and not arg:
            raise ValueError(f"línea {i}: '{verbo}' necesita un argumento")
        if verbo == "paquete":
            paquete = arg
            continue
        pasos.append((i, verbo, arg))
    return paquete, pasos


def _inicio(lugar: Lugar, serial: str, pkg: str):
    if not pkg:
        return False, ("falta el paquete: agregá 'paquete <pkg>' al guión o pasá "
                       "el parámetro 'paquete' a adb_guion")
    # logcat grande y limpio (que no se pierda la evidencia) + estado conocido.
    T.ejecutar(lugar, ["adb", "-s", serial, "logcat", "-G", "16M"])
    T.ejecutar(lugar, ["adb", "-s", serial, "logcat", "-c"])
    T.ejecutar(lugar, ["adb", "-s", serial, "shell", "am", "force-stop", pkg])
    M.adb_relanzar(lugar, serial, pkg)
    time.sleep(1.5)
    return True, f"estado inicial ({pkg}): logcat 16M/limpio, force-stop + relanzar"


def _paso(lugar, serial, verbo, arg, pkg, permitidos, capturas):
    if verbo == "inicio":
        return _inicio(lugar, serial, pkg)
    if verbo == "permitir":
        permitidos.add(M._norm(arg))
        return True, f"habilitado para tap: '{arg}'"
    if verbo == "tap":
        conf = M.es_peligroso(arg) and (M._norm(arg) in permitidos)
        r = M.adb_tap_texto(lugar, serial, arg, timeout=12, confirmado=conf)
        if r.startswith("BLOQUEADO"):
            r += f" (agregá 'permitir {arg}' antes del tap para habilitarlo)"
        return r.startswith("OK:"), r
    if verbo == "esperar":
        r = M.adb_esperar(lugar, serial, texto=arg, timeout=15)
        return r.startswith("apareció"), r
    if verbo == "esperar_log":
        r = M.adb_esperar(lugar, serial, patron_log=arg, timeout=20)
        return r.startswith("log OK"), r
    if verbo == "verificar":
        nodos = _nodos(lugar, serial)
        hay = M._buscar_nodo(nodos, arg, True) is not None
        return hay, ("presente" if hay else f'FALTA "{arg}"')
    if verbo == "no_debe":
        nodos = _nodos(lugar, serial)
        hay = M._buscar_nodo(nodos, arg, True) is not None
        return (not hay), (f'PRESENTE, no debía "{arg}"' if hay else "ausente, ok")
    if verbo == "atras":
        T.ejecutar(lugar, ["adb", "-s", serial, "shell", "input", "keyevent", "4"])
        time.sleep(0.4)
        return True, "BACK"
    if verbo == "captura":
        try:
            capturas.append(M.adb_captura(lugar, serial))
            return True, "captura tomada"
        except Exception as e:
            return True, f"(captura falló: {e})"  # opt-in, no rompe el guión
    return False, f"verbo no implementado: {verbo}"


def correr(lugar: Lugar, serial: str, ruta: str, origen: Lugar | None = None,
           paquete: str = "", desde: int = 1) -> dict:
    """Corre el guión. Devuelve dict {ok, texto, imagenes, [parcial, siguiente]}."""
    from . import archivos as A
    org = origen if origen is not None else lugar
    try:
        texto = A._leer_bytes(org, ruta).decode("utf-8", "replace")
    except Exception as e:
        return {"ok": False, "texto": f"no pude leer el guión {ruta}: {e}",
                "imagenes": []}
    try:
        pkg_dir, pasos = parsear(texto)
    except ValueError as e:
        return {"ok": False, "texto": f"guión inválido: {e}", "imagenes": []}
    if not pasos:
        return {"ok": False, "texto": "el guión no tiene pasos", "imagenes": []}
    pkg = paquete or pkg_dir or ""
    permitidos: set = set()
    capturas: list = []
    traza: list = []
    total = len(pasos)
    idx = min(max(1, desde), total)  # 1-based sobre la lista de pasos
    t0 = time.time()
    while idx <= total:
        num, verbo, arg = pasos[idx - 1]
        ok, msg = _paso(lugar, serial, verbo, arg, pkg, permitidos, capturas)
        traza.append(f"{idx:>2}/{total} {verbo} {arg}  -> {msg}")
        if not ok:
            return _fallo(lugar, serial, idx, total, verbo, arg, msg, traza, capturas)
        idx += 1
        if idx <= total and (time.time() - t0) >= _PRESUPUESTO:
            cuerpo = (f"EN PROGRESO: {idx - 1}/{total} pasos ok en esta llamada. "
                      f"El cliente MCP corta las llamadas largas; seguí con "
                      f"adb_guion(..., desde={idx}).\n" + "\n".join(traza[-6:]))
            return {"ok": True, "parcial": True, "siguiente": idx,
                    "texto": cuerpo, "imagenes": capturas}
    return {"ok": True, "texto": f"GUIÓN OK: {total}/{total} pasos verdes "
            f"(serial {serial}).", "imagenes": capturas}


def _fallo(lugar, serial, idx, total, verbo, arg, msg, traza, capturas):
    partes = [f"GUIÓN FALLÓ en el paso {idx}/{total}: {verbo} {arg}",
              f"  motivo: {msg}", "", "traza:"]
    partes += traza
    try:
        nodos = M._ui_nodos(lugar, serial)
        textos = sorted({(n["desc"] or n["texto"]) for n in nodos
                         if (n["desc"] or n["texto"])})
        partes += ["", "textos en pantalla ahora:"] + [f"  - {t}" for t in textos]
    except Exception as e:
        partes.append(f"(no pude volcar la UI: {e})")
    r = T.ejecutar(lugar, ["adb", "-s", serial, "logcat", "-d", "-t", "150"],
                   timeout=20)
    partes += ["", "logcat (reciente):", (r.salida or "")[-3000:]]
    imgs = list(capturas)
    try:
        imgs.append(M.adb_captura(lugar, serial))
    except Exception:
        pass
    return {"ok": False, "texto": "\n".join(partes), "imagenes": imgs}
