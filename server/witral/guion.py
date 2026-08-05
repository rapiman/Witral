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
  humano <mensaje>     PAUSA el guión y pide al humano ese paso (tarjeta, PIN
                       físico); se retoma con desde=siguiente cuando esté hecho
  pin <dígitos|$var>   teclea un PIN SIN valor de seguridad (menú/config), solo
                       con confirmado=True en la llamada; el valor se pasa como
                       $variable en 'valores' (vive solo en la conversación) y
                       se enmascara en toda salida

Como el cliente MCP corta las llamadas largas (~45s), el runner corre con un
presupuesto de tiempo por llamada; si un guión largo no termina, devuelve
'EN PROGRESO: seguí con desde=K' y se retoma desde ese paso.
"""

from __future__ import annotations

import re
import time

from .config import Lugar
from . import movil as M
from . import transporte as T


_VERBOS_ARG = {"paquete", "tap", "escribir", "permitir", "esperar", "esperar_log",
               "verificar", "no_debe", "humano", "pin"}

# Variables $nombre en los args de un guión: se expanden con el parámetro
# 'valores' de adb_guion. El valor vive SOLO en la llamada (la conversación):
# ni en el .txt del guión, ni en disco, ni en el repo. Un '$' de regex al final
# de un patrón (p. ej. 'APROBADO$') no matchea (no lo sigue un identificador);
# para un '$' literal seguido de letras en un patrón, usar '[$]'.
_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

# Aleatorios por corrida (QA con variedad): dos formas.
# - En 'valores': monto=rnd(10000,30000,500) — se resuelve UNA vez por corrida
#   y todos los $monto usan ese mismo número (consistencia entre pasos).
# - Inline en un arg: $rnd(10000,30000,500) o $rnd_opcion(10%,20%,30%) — cada
#   ocurrencia rueda su propio valor al ejecutarse su paso.
# Lo elegido queda visible: en la traza (args expandidos) y en la línea final
# del GUIÓN OK ("Aleatorios: ..."). Si una corrida con rnd se pausa, al
# reanudar conviene fijar en 'valores' los ya resueltos (salen en la traza).
_RND_RE = re.compile(r"^rnd\(\s*(-?\d+)\s*,\s*(-?\d+)\s*(?:,\s*(\d+)\s*)?\)$")
_RND_OP_RE = re.compile(r"^rnd_opcion\((.*)\)$")
_RND_INLINE_RE = re.compile(r"\$(rnd(?:_opcion)?\([^)]*\))")


def _resolver_rnd(expr: str) -> str | None:
    """Evalúa 'rnd(min,max[,paso])' (entero alineado al paso) o
    'rnd_opcion(a,b,c)' (una de las opciones; comillas opcionales). Devuelve
    None si no es una expresión rnd; ValueError si lo es pero está mal."""
    import random
    e = expr.strip()
    m = _RND_RE.match(e)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        paso = int(m.group(3) or 1)
        if paso <= 0 or b < a:
            raise ValueError(f"rnd inválido (pide min<=max y paso>0): {expr}")
        return str(a + paso * random.randint(0, (b - a) // paso))
    m = _RND_OP_RE.match(e)
    if m:
        ops = [o.strip().strip('"').strip("'") for o in m.group(1).split(",")]
        ops = [o for o in ops if o]
        if not ops:
            raise ValueError(f"rnd_opcion sin opciones: {expr}")
        return random.choice(ops)
    return None
_VERBOS_SOLO = {"inicio", "atras", "captura", "limpiar_log"}

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


def _arg_timeout(arg: str, default: int):
    """Si el arg de esperar/esperar_log empieza con un entero, lo usa como
    timeout en segundos y devuelve (timeout, resto). Ej.: 'esperar 35 Monto de
    venta' -> (35, 'Monto de venta'). Sin entero al inicio, usa el default."""
    partes = arg.split(None, 1)
    if partes and partes[0].isdigit():
        return int(partes[0]), (partes[1].strip() if len(partes) > 1 else "")
    return default, arg


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

    # SEN20260804 se espera el FOCO DE ENTRADA, no un rótulo en pantalla.
    #
    # "El texto ya se ve" NO significa "la ventana ya recibe toques". Medido en
    # un A920Pro: el árbol de vistas se puede volcar a los 2s de relanzar, pero
    # el foco recién llega a los ~7.4s ("Displayed +7s751ms"). Todo tap antes de
    # eso se lo traga el sistema EN SILENCIO — `input tap` no falla, la tool
    # responde OK — y el guión se cae dos o tres pasos después, con un mensaje
    # que apunta al lugar equivocado. Eso costó varias corridas hasta entenderlo.
    #
    # Se hace acá, y no en cada guión, justamente para que ningún guión tenga
    # que acordarse. Si el evento no aparece, no se aborta: se sigue y que falle
    # el paso real, que da mejor diagnóstico que un error genérico de arranque.
    r = M.adb_esperar(lugar, serial,
                      patron_log=r"Input focus has changed to Window.*" + re.escape(pkg),
                      timeout=40)
    detalle = "foco confirmado" if r.startswith("log OK") else f"sin evento de foco ({r})"
    return True, f"estado inicial ({pkg}): logcat 16M/limpio, force-stop + relanzar, {detalle}"


def _paso(lugar, serial, verbo, arg, pkg, permitidos, capturas):
    if verbo == "inicio":
        return _inicio(lugar, serial, pkg)
    if verbo == "limpiar_log":
        # Estado de log conocido SIN force-stop (rápido): agranda el buffer y lo
        # limpia, para poder asertar por logcat sin falso positivo de una corrida
        # anterior. Es lo que hace `inicio` con el log, pero sin el cold-start.
        T.ejecutar(lugar, ["adb", "-s", serial, "logcat", "-G", "16M"])
        T.ejecutar(lugar, ["adb", "-s", serial, "logcat", "-c"])
        return True, "logcat 16M/limpio"
    if verbo == "permitir":
        permitidos.add(M._norm(arg))
        return True, f"habilitado para tap: '{arg}'"
    if verbo == "tap":
        conf = M.es_peligroso(arg) and (M._norm(arg) in permitidos)
        r = M.adb_tap_texto(lugar, serial, arg, timeout=12, confirmado=conf)
        if r.startswith("BLOQUEADO"):
            r += f" (agregá 'permitir {arg}' antes del tap para habilitarlo)"
        return r.startswith("OK:"), r
    if verbo == "escribir":
        r = M.adb_escribir(lugar, serial, arg)
        return r.startswith("OK:"), r
    if verbo == "esperar":
        to, txt = _arg_timeout(arg, 15)
        r = M.adb_esperar(lugar, serial, texto=txt, timeout=to)
        return r.startswith("apareció"), r
    if verbo == "esperar_log":
        to, pat = _arg_timeout(arg, 20)
        r = M.adb_esperar(lugar, serial, patron_log=pat, timeout=to)
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


def _pausa(idx: int, total: int, traza: list, capturas: list, nota: str = "") -> dict:
    """Devuelve el resultado de PAUSA (parcial): el guión no terminó en esta
    llamada; se retoma con `desde=idx`."""
    extra = f" ({nota})" if nota else ""
    cuerpo = (f"EN PROGRESO: {idx - 1}/{total} pasos ok en esta llamada{extra}. "
              f"El cliente MCP corta las llamadas largas; seguí con "
              f"adb_guion(..., desde={idx}).\n" + "\n".join(traza[-6:]))
    return {"ok": True, "parcial": True, "siguiente": idx,
            "texto": cuerpo, "imagenes": capturas}


def correr(lugar: Lugar, serial: str, ruta: str, origen: Lugar | None = None,
           paquete: str = "", desde: int = 1, confirmado: bool = False,
           valores: str = "") -> dict:
    """Corre el guión. Devuelve dict {ok, texto, imagenes, [parcial, siguiente]}.
    'valores': pares "nombre=valor" separados por ';' que expanden las
    variables $nombre del guión — el valor vive solo en la llamada.
    'confirmado': habilita los pasos `pin` de esta corrida (un dale por corrida)."""
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
    # Variables $nombre: parsear 'valores' y validar ANTES de tocar el device
    # (si falta una, no se ejecutó nada y el mensaje dice cuál).
    vals: dict = {}
    for par in re.split(r"[;\n]", valores or ""):
        par = par.strip()
        if not par:
            continue
        if "=" not in par:
            return {"ok": False, "imagenes": [], "texto":
                    f"'valores' inválido: '{par}' (formato nombre=valor, "
                    f"separados por ';')."}
        k, v = par.split("=", 1)
        vals[k.strip()] = v.strip()
    aleatorios: list = []
    try:
        # rnd en 'valores': un valor por corrida, compartido por todos los $var.
        for k in list(vals):
            r = _resolver_rnd(vals[k])
            if r is not None:
                vals[k] = r
                aleatorios.append(f"{k}={r}")
        # rnd inline: validar la sintaxis ANTES de tocar el device.
        for _, _, a in pasos:
            for m in _RND_INLINE_RE.finditer(a):
                _resolver_rnd(m.group(1))
    except ValueError as e:
        return {"ok": False, "imagenes": [], "texto": f"guión inválido: {e}"}
    # Para detectar variables faltantes, primero sacar las expresiones rnd
    # inline ($rnd no es una variable que haya que pasar en 'valores').
    sin_rnd = [_RND_INLINE_RE.sub("", a) for _, _, a in pasos]
    usadas = {m.group(1) for a in sin_rnd for m in _VAR_RE.finditer(a)}
    faltan = sorted(usadas - set(vals))
    if faltan:
        return {"ok": False, "imagenes": [], "texto":
                "faltan valores para: $" + ", $".join(faltan) +
                " — pasalos en 'valores' (\"nombre=valor;...\"). "
                "No se ejecutó nada."}

    def _exp(a: str) -> str:
        # Inline primero (cada ocurrencia rueda su valor y queda registrada),
        # después las variables con nombre.
        def _roll(m):
            v = _resolver_rnd(m.group(1))
            aleatorios.append(f"{m.group(1)} -> {v}")
            return v
        a = _RND_INLINE_RE.sub(_roll, a)
        return _VAR_RE.sub(lambda mm: vals[mm.group(1)], a)

    def _mascarar(msj: str, secreto: str) -> str:
        """Borra el valor del PIN (y sus segmentos de dígitos) de un mensaje
        antes de mostrarlo: el valor vive en la conversación, no en la salida."""
        for seg in [secreto] + [s for s in secreto.split("+") if s.strip().isdigit()]:
            if seg:
                msj = msj.replace(seg, "····")
        return msj

    permitidos: set = set()
    capturas: list = []
    traza: list = []
    total = len(pasos)
    idx = min(max(1, desde), total)  # 1-based sobre la lista de pasos
    t0 = time.time()
    while idx <= total:
        num, verbo, arg = pasos[idx - 1]
        arg = _exp(arg)  # variables $nombre resueltas (pin se enmascara al mostrar)

        # Paso PIN: dígitos que la máquina SÍ puede tipear (PIN de menú/config
        # sin valor de seguridad), pero solo con el visto del usuario: requiere
        # confirmado=True en la llamada (un dale habilita todos los pin de la
        # corrida). El PIN de tarjeta en teclado seguro sigue siendo `humano`.
        if verbo == "pin":
            if not confirmado:
                traza.append(f"{idx:>2}/{total} pin ····  -> pausa (falta confirmado)")
                cuerpo = (f"PASO PIN ({idx}/{total}): la máquina puede tipearlo, "
                          f"pero requiere el visto del usuario. Con su dale, "
                          f"reintentá con confirmado=True y desde={idx}; o se "
                          f"tipea a mano y se sigue con desde={idx + 1}.\n"
                          + "\n".join(traza[-6:]))
                return {"ok": True, "parcial": True, "siguiente": idx,
                        "texto": cuerpo, "imagenes": capturas}
            r = M.adb_escribir(lugar, serial, arg)
            ok = r.startswith("OK:")
            traza.append(f"{idx:>2}/{total} pin ····  -> "
                         + ("OK (tecleado; valor no mostrado)" if ok
                            else _mascarar(r, arg)))
            if not ok:
                return _fallo(lugar, serial, idx, total, "pin", "····",
                              _mascarar(r, arg), traza, capturas)
            idx += 1
            if idx <= total and (time.time() - t0) >= _PRESUPUESTO:
                return _pausa(idx, total, traza, capturas)
            continue

        # Paso HUMANO: el guión se pausa y devuelve el pedido tal cual — hay
        # pasos que la máquina no debe dar (tarjeta real, PIN físico). El humano
        # lo hace y el guión se retoma con desde=idx+1, contexto intacto.
        if verbo == "humano":
            traza.append(f"{idx:>2}/{total} humano {arg}  -> pausa (paso humano)")
            cuerpo = (f"PASO HUMANO ({idx}/{total}): {arg}\n"
                      f"Cuando esté hecho, seguí con adb_guion(..., "
                      f"desde={idx + 1}).\n" + "\n".join(traza[-6:]))
            return {"ok": True, "parcial": True, "siguiente": idx + 1,
                    "texto": cuerpo, "imagenes": capturas}

        # Pasos de espera: acotar su timeout al PRESUPUESTO restante de la llamada
        # para no pasarse del corte del cliente MCP (~45-60s). Si el presupuesto lo
        # corta ANTES de su timeout real, se PAUSA (no falla) y se reintenta este
        # mismo paso al reanudar — así una pantalla impaciente sobrevive al gap.
        if verbo in ("esperar", "esperar_log"):
            restante = _PRESUPUESTO - (time.time() - t0)
            if restante < 2 and idx > 1:
                return _pausa(idx, total, traza, capturas)
            default = 15 if verbo == "esperar" else 20
            to_req, txt = _arg_timeout(arg, default)
            efectivo = min(to_req, max(2, int(restante)))
            if verbo == "esperar":
                r = M.adb_esperar(lugar, serial, texto=txt, timeout=efectivo)
                ok = r.startswith("apareció")
            else:
                r = M.adb_esperar(lugar, serial, patron_log=txt, timeout=efectivo)
                ok = r.startswith("log OK")
            traza.append(f"{idx:>2}/{total} {verbo} {arg}  -> {r}")
            if ok:
                idx += 1
                continue
            if efectivo < to_req:
                # Cortado por presupuesto (no por su timeout real): pausar.
                return _pausa(idx, total, traza, capturas,
                              nota=f"esperando, {efectivo}s sin éxito; sigue")
            return _fallo(lugar, serial, idx, total, verbo, arg, r, traza, capturas)

        ok, msg = _paso(lugar, serial, verbo, arg, pkg, permitidos, capturas)
        traza.append(f"{idx:>2}/{total} {verbo} {arg}  -> {msg}")
        if not ok:
            return _fallo(lugar, serial, idx, total, verbo, arg, msg, traza, capturas)
        idx += 1
        if idx <= total and (time.time() - t0) >= _PRESUPUESTO:
            return _pausa(idx, total, traza, capturas)
    extra_rnd = f" Aleatorios: {', '.join(aleatorios)}." if aleatorios else ""
    return {"ok": True, "texto": f"GUIÓN OK: {total}/{total} pasos verdes "
            f"(serial {serial}).{extra_rnd}", "imagenes": capturas}


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
