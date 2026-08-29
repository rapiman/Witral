"""
Trabajos en segundo plano (buzón asíncrono): lanzar un comando largo sin
bloquear el transporte MCP, consultar su estado por id, y matarlo si hace
falta. Resuelve el freno de los timeouts del cliente (~60s) con trabajos de
minutos: run_async devuelve al instante y run_status se consulta por polling.

El estado vive en DISCO (.witral/jobs/<id>/ del lugar): cmd.txt, pid, out.log,
err.log y — al terminar — codigo. Así sobrevive a reinicios del servidor MCP y
se puede consultar desde cualquier conversación.

El detach usa el patrón que demostró funcionar en la práctica:
- unix/remoto: setsid sh -c '...' < /dev/null &  (el propio sh de la nueva
  sesión registra su pid con $$, que es también el líder de grupo: matar el
  grupo entero es kill -- -pid).
- Windows local: un .cmd lanzado DETACHED con grupo de proceso propio
  (taskkill /T /F lo mata con todo su árbol).
El comando corre con cwd en la raíz del lugar.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import time
from pathlib import Path

from .config import Lugar
from . import transporte as T


_q = T.comillas  # comilla POSIX: origen único en transporte.comillas


def _nuevo_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def _dir_jobs_local(lugar: Lugar) -> Path:
    return Path(lugar.raiz) / ".witral" / "jobs"


_DIR_REMOTO = ".witral/jobs"  # relativo al home del lugar remoto

# Tope de espera POR LLAMADA de run_esperar. El cliente MCP corta las llamadas
# largas (~45s), así que no se puede bloquear 10 minutos de un saque: cada
# run_esperar espera a lo sumo esto y, si el trabajo sigue, pide volver a
# llamar. Aun así colapsa el polling: una llamada cubre ~40s y vuelve al
# instante cuando el trabajo termina (chequeo cada 1-3s), en vez de decenas de
# sleep+run_status a ciegas.
_TOPE_ESPERA = 40


# --- Lanzar -------------------------------------------------------------------

def lanzar(lugar: Lugar, comando: str) -> str:
    """Lanza 'comando' detached en el lugar. Devuelve el id del trabajo."""
    jid = _nuevo_id()
    if lugar.es_local:
        base = _dir_jobs_local(lugar) / jid
        base.mkdir(parents=True, exist_ok=True)
        (base / "cmd.txt").write_text(comando, encoding="utf-8")
        out, err, cod = base / "out.log", base / "err.log", base / "codigo"
        if os.name == "nt":
            # Batch: %errorlevel% se expande línea a línea, así que tras el
            # bloque ya trae el código del comando. chcp 65001 => salida UTF-8.
            # El comando va en SU PROPIO .cmd y se invoca con CALL. Motivo: en
            # batch, invocar otro .bat/.cmd SIN `call` TRANSFIERE el control y
            # el script que llama nunca retoma. Con el comando inline, un
            # `gradlew.bat` terminaba el wrapper entero y la línea que escribe
            # `codigo` no llegaba a ejecutarse: el build terminaba bien, el
            # proceso desaparecía y el trabajo quedaba para siempre "sin código"
            # (de ahí el estado contradictorio que veía run_esperar). Con CALL,
            # el control vuelve y el errorlevel del comando se registra.
            interno = base / "comando.cmd"
            interno.write_text(f"@echo off\r\n@chcp 65001 >nul\r\n{comando}\r\n",
                               encoding="utf-8")
            bat = base / "lanzar.cmd"
            bat.write_text(
                "@echo off\r\n"
                f"call \"{interno}\" > \"{out}\" 2> \"{err}\"\r\n"
                f"echo %errorlevel% > \"{cod}\"\r\n",
                encoding="utf-8",
            )
            # CREATE_NO_WINDOW (consola OCULTA propia) y NO DETACHED_PROCESS:
            # son excluyentes, y sin consola las console-apps (ping, timeout,
            # el host de powershell) corren mudas o mueren al instante.
            # Verificado con A/B: DETACHED => out vacío; NO_WINDOW => captura OK.
            flags = (subprocess.CREATE_NEW_PROCESS_GROUP
                     | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
            # Los trabajos también heredan el fix de la JVM bajo sandbox: un
            # build lanzado por run_async no tiene por qué comportarse distinto
            # de uno lanzado por gradle_build.
            entorno = dict(os.environ)
            entorno.update(T.entorno_jvm(lugar.raiz))
            proc = subprocess.Popen(
                ["cmd", "/c", str(bat)], cwd=lugar.raiz, env=entorno,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=flags,
            )
        else:
            linea = (f"({comando}) > {_q(str(out))} 2> {_q(str(err))}; "
                     f"echo $? > {_q(str(cod))}")
            proc = subprocess.Popen(
                ["sh", "-c", linea], cwd=lugar.raiz,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
        (base / "pid").write_text(str(proc.pid), encoding="ascii")
        return jid

    # Remoto (unix). Las rutas del job son relativas al HOME (donde arranca el
    # shell de exec_command); el cd a la raíz va DENTRO del subshell del comando
    # para no romper las redirecciones. El sh de la nueva sesión registra su
    # propio pid ($$ = líder de la sesión y del grupo).
    base = f"{_DIR_REMOTO}/{jid}"
    cd = f"cd {_q(lugar.raiz)} && " if lugar.raiz else ""
    interno = (f"echo $$ > {base}/pid; "
               f"( {cd}( {comando} ) ) > {base}/out.log 2> {base}/err.log; "
               f"echo $? > {base}/codigo")
    linea = (f"mkdir -p {_q(base)} && printf %s {_q(comando)} > {_q(base + '/cmd.txt')}; "
             f"setsid sh -c {_q(interno)} < /dev/null > /dev/null 2>&1 & "
             f"echo lanzado")
    r = T.ejecutar(lugar, linea, timeout=30)
    if not r.ok:
        raise T.TransporteError(f"no se pudo lanzar el trabajo: {r.error or r.salida}")
    return jid


# --- Estado -------------------------------------------------------------------

def _pid_vivo_local(pid: int) -> bool:
    if os.name == "nt":
        try:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                               capture_output=True, timeout=15)
            return str(pid).encode() in (r.stdout or b"")
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _cola_texto(texto: str, n: int) -> str:
    lineas = texto.splitlines()
    return "\n".join(lineas[-n:]) if lineas else ""


# Marcas de cierre que un log deja cuando el trabajo llegó al final por sus
# propios medios. Sirven para no declarar "abortado" un build que terminó bien.
_MARCAS_FIN = (
    ("BUILD SUCCESSFUL", "0"),
    ("BUILD FAILED", "distinto de 0"),
    ("FAILURE: Build failed", "distinto de 0"),
)

# Margen tras lanzar durante el cual la ausencia de pid es "recién lanzado" y
# no "murió": el pid se escribe un instante después de arrancar el proceso.
_GRACIA_LANZADO = 10.0


def _marca_fin(base) -> tuple[str, str]:
    """(codigo_inferido, marca) leyendo el final de los logs; ("","") si nada."""
    for nombre in ("out.log", "err.log"):
        ruta = base / nombre
        if not ruta.exists():
            continue
        try:
            cola = _cola_texto(
                ruta.read_text(encoding="utf-8", errors="replace"), 40)
        except OSError:
            continue
        for marca, cod in _MARCAS_FIN:
            if marca in cola:
                return cod, marca
    return "", ""


def _diagnostico_local(base) -> tuple[str, str, str]:
    """
    ÚNICO lugar que decide en qué estado está un trabajo local. Devuelve
    (estado, codigo, detalle) con estado en:
      no_existe | corriendo | terminado | terminado_sin_codigo

    Todo el texto que se le muestra a quien llama se deriva de aquí, para que no
    puedan volver a convivir tres afirmaciones incompatibles ("sin código",
    "BUILD SUCCESSFUL" y "sigue corriendo") en la misma respuesta.
    """
    if not base.exists():
        return "no_existe", "", ""
    ruta_cod = base / "codigo"
    if ruta_cod.exists():
        try:
            return "terminado", ruta_cod.read_text(
                encoding="utf-8", errors="replace").strip(), ""
        except OSError:
            pass
    pid = None
    try:
        pid = int((base / "pid").read_text().strip())
    except Exception:
        pass
    if pid and _pid_vivo_local(pid):
        return "corriendo", "", f"pid {pid}"
    if pid is None:
        # Sin pid todavía: recién lanzado, no muerto (salvo que ya pasó rato).
        try:
            edad = time.time() - base.stat().st_mtime
        except OSError:
            edad = _GRACIA_LANZADO + 1
        if edad < _GRACIA_LANZADO:
            return "corriendo", "", "recién lanzado, pid aún no registrado"
    cod, marca = _marca_fin(base)
    if cod:
        return ("terminado_sin_codigo", cod,
                f"el proceso ya no existe y el log cierra en '{marca}'")
    return ("terminado_sin_codigo", "",
            "el proceso ya no existe y el log no tiene marca de cierre "
            "(abortado, o el wrapper murió antes de registrar el código)")


def estado(lugar: Lugar, jid: str, lineas: int = 40) -> str:
    """Estado + últimas líneas de salida de un trabajo."""
    if lugar.es_local:
        base = _dir_jobs_local(lugar) / jid
        if not base.exists():
            return f"No existe el trabajo '{jid}' en {lugar.nombre}. Ver run_status sin id."
        partes = [f"Trabajo {jid} en {lugar.nombre}"]
        try:
            partes.append("cmd: " + (base / "cmd.txt").read_text(encoding="utf-8").strip())
        except Exception:
            pass
        est, cod, detalle = _diagnostico_local(base)
        if est == "terminado":
            partes.append(f"estado: TERMINADO, código {cod}")
        elif est == "corriendo":
            partes.append(f"estado: CORRIENDO ({detalle})")
        elif cod:
            partes.append(f"estado: TERMINADO, código {cod} (inferido: {detalle}; "
                          f"el wrapper no alcanzó a registrarlo)")
        else:
            partes.append(f"estado: TERMINADO sin código — {detalle}")
        for nombre in ("out.log", "err.log"):
            ruta = base / nombre
            if ruta.exists():
                txt = ruta.read_text(encoding="utf-8", errors="replace")
                cola = _cola_texto(txt, lineas)
                partes.append(f"--- {nombre} (últimas {lineas} de "
                              f"{len(txt.splitlines())} líneas) ---\n{cola}"
                              if cola else f"--- {nombre} --- (vacío)")
        return "\n".join(partes)

    b = f"{_DIR_REMOTO}/{jid}"
    linea = (
        f"b={_q(b)}; "
        f"if [ ! -d \"$b\" ]; then echo \"No existe el trabajo {jid}\"; exit 0; fi; "
        f"echo \"Trabajo {jid} en {lugar.nombre}\"; "
        f"echo \"cmd: $(cat \"$b/cmd.txt\" 2>/dev/null)\"; "
        f"if [ -f \"$b/codigo\" ]; then echo \"estado: TERMINADO, código $(cat \"$b/codigo\")\"; "
        f"else pid=$(cat \"$b/pid\" 2>/dev/null); "
        f"if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then "
        f"echo \"estado: CORRIENDO (pid $pid)\"; "
        f"else echo \"estado: TERMINADO sin código — el proceso ya no existe "
        f"(abortado, o el wrapper murió antes de registrarlo)\"; fi; fi; "
        f"echo \"--- out.log (últimas {lineas}) ---\"; tail -n {lineas} \"$b/out.log\" 2>/dev/null; "
        f"echo \"--- err.log (últimas {lineas}) ---\"; tail -n {lineas} \"$b/err.log\" 2>/dev/null"
    )
    r = T.ejecutar(lugar, linea, timeout=30)
    return r.salida if r.ok else f"error: {r.error or r.salida}"


# --- Esperar (bloqueo del lado servidor) -------------------------------------

def _estado_rapido(lugar: Lugar, jid: str) -> str:
    """Chequeo LIVIANO del estado de un trabajo: 'no_existe'|'terminado'|'corriendo'.
    Local: solo mira archivos en disco (barato). Remoto: un SSH corto."""
    if lugar.es_local:
        return _diagnostico_local(_dir_jobs_local(lugar) / jid)[0]
    b = f"{_DIR_REMOTO}/{jid}"
    # Mismo criterio que en local: la ausencia de 'codigo' NO alcanza para decir
    # "corriendo"; hay que mirar si el proceso sigue vivo.
    linea = (f"b={_q(b)}; "
             f"if [ ! -d \"$b\" ]; then echo no_existe; "
             f"elif [ -f \"$b/codigo\" ]; then echo terminado; "
             f"else pid=$(cat \"$b/pid\" 2>/dev/null); "
             f"if [ -z \"$pid\" ]; then echo corriendo; "
             f"elif kill -0 \"$pid\" 2>/dev/null; then echo corriendo; "
             f"else echo terminado_sin_codigo; fi; fi")
    r = T.ejecutar(lugar, linea, timeout=20)
    est = (r.salida or "").strip()
    return est if est in ("no_existe", "terminado", "corriendo",
                          "terminado_sin_codigo") else "corriendo"


def _texto_logs(lugar: Lugar, jid: str) -> str:
    """Contenido actual de out.log + err.log del trabajo (para buscar en él)."""
    if lugar.es_local:
        base = _dir_jobs_local(lugar) / jid
        partes = []
        for nombre in ("out.log", "err.log"):
            ruta = base / nombre
            if ruta.exists():
                try:
                    partes.append(ruta.read_text(encoding="utf-8",
                                                 errors="replace"))
                except OSError:
                    pass
        return "\n".join(partes)
    b = f"{_DIR_REMOTO}/{jid}"
    r = T.ejecutar(lugar, f"cat {b}/out.log {b}/err.log 2>/dev/null", timeout=20)
    return r.salida or ""


def _buscar_patron(lugar: Lugar, jid: str, patron) -> str:
    """Primera línea del log que matchea 'patron', o "" si todavía ninguna."""
    for linea in _texto_logs(lugar, jid).splitlines():
        if patron.search(linea):
            return linea.strip()
    return ""


def esperar(lugar: Lugar, jid: str, hasta_segundos: int = 600,
            lineas: int = 40, hasta_patron: str = "") -> str:
    """
    Bloquea del lado de Witral hasta que el trabajo termine, y devuelve su
    estado final. Evita el polling manual con sleep+run_status.

    Como el cliente MCP corta las llamadas largas, cada llamada espera a lo
    sumo _TOPE_ESPERA s: si el trabajo termina antes, vuelve al instante; si
    sigue corriendo al llegar al tope, devuelve el estado parcial e indica
    volver a llamar. 'hasta_segundos' es el techo que pide el usuario, pero se
    acota a _TOPE_ESPERA por llamada.
    """
    presupuesto = min(max(1, int(hasta_segundos)), _TOPE_ESPERA)
    intervalo = 1.0 if lugar.es_local else 3.0
    rx = None
    if hasta_patron:
        import re as _re
        try:
            rx = _re.compile(hasta_patron)
        except _re.error as e:
            return (f"error: 'hasta_patron' no es una regex válida ({e}). "
                    f"Para alternativas, la barra vertical: "
                    f"\"SONDA IDENTICA|SONDA DIFIERE\".")
    t0 = time.time()
    while True:
        # El patrón se mira ANTES que el estado: si la línea que se espera ya
        # salió, no tiene sentido seguir esperando a que el proceso muera.
        if rx is not None:
            linea = _buscar_patron(lugar, jid, rx)
            if linea:
                return (f"[run_esperar: MATCH de /{hasta_patron}/ tras "
                        f"~{int(time.time() - t0)}s]\n{linea}\n\n"
                        + estado(lugar, jid, lineas))
        est = _estado_rapido(lugar, jid)
        if est == "no_existe":
            return (f"No existe el trabajo '{jid}' en {lugar.nombre}. "
                    f"Ver run_status sin id.")
        if est in ("terminado", "terminado_sin_codigo"):
            # TERMINAL: se devuelve el estado y NUNCA el pie de "volver a
            # llamar". Que el pie sea inalcanzable desde aquí es justamente el
            # arreglo: antes se decidía por reloj, sin mirar este estado.
            final = estado(lugar, jid, lineas)
            if rx is not None:
                final += (f"\n\n[run_esperar: el trabajo TERMINÓ sin que "
                          f"apareciera /{hasta_patron}/ en los logs.]")
            return final
        transcurrido = time.time() - t0
        if transcurrido >= presupuesto:
            parcial = estado(lugar, jid, lineas)
            extra = (f" Tampoco apareció aún /{hasta_patron}/."
                     if rx is not None else "")
            sugerencia = ("" if rx is not None else
                          " Si se sabe qué línea se está esperando, "
                          "'hasta_patron' corta en cuanto aparece y evita la "
                          "cadena de llamadas.")
            return (parcial + f"\n\n[run_esperar: sigue CORRIENDO tras "
                    f"~{int(transcurrido)}s.{extra} El cliente MCP corta las "
                    f"llamadas largas, por eso la espera se topa en "
                    f"~{_TOPE_ESPERA}s. Volver a llamar run_esperar(id=\""
                    f"{jid}\", donde=\"{lugar.nombre}\") para seguir "
                    f"esperando.{sugerencia}]")
        # No pasarse del presupuesto en el último sleep.
        time.sleep(min(intervalo, max(0.2, presupuesto - transcurrido)))


def listar(lugar: Lugar, maximo: int = 15) -> str:
    """Últimos trabajos del lugar con su estado resumido."""
    if lugar.es_local:
        raiz = _dir_jobs_local(lugar)
        if not raiz.exists():
            return f"Sin trabajos en {lugar.nombre}."
        dirs = sorted((d for d in raiz.iterdir() if d.is_dir()),
                      key=lambda d: d.name, reverse=True)[:maximo]
        if not dirs:
            return f"Sin trabajos en {lugar.nombre}."
        out = []
        for d in dirs:
            e, cod, _det = _diagnostico_local(d)
            if e == "terminado":
                est = f"terminado({cod})"
            elif e == "corriendo":
                est = "corriendo"
            else:
                est = f"terminado sin código({cod or '?'})"
            out.append(f"- {d.name}  {est}")
        return f"Trabajos en {lugar.nombre}:\n" + "\n".join(out)
    linea = (
        f"if [ ! -d {_q(_DIR_REMOTO)} ]; then echo 'Sin trabajos'; exit 0; fi; "
        f"for d in $(ls -1t {_q(_DIR_REMOTO)} 2>/dev/null | head -{maximo}); do "
        f"b={_q(_DIR_REMOTO)}/$d; "
        f"if [ -f \"$b/codigo\" ]; then echo \"- $d  terminado($(cat \"$b/codigo\"))\"; "
        f"else echo \"- $d  corriendo?\"; fi; done"
    )
    r = T.ejecutar(lugar, linea, timeout=30)
    return (f"Trabajos en {lugar.nombre}:\n" + r.salida) if r.ok else f"error: {r.error}"


# --- Matar --------------------------------------------------------------------

def matar(lugar: Lugar, jid: str) -> str:
    """Mata el ÁRBOL de procesos del trabajo y marca su código como 'matado'."""
    if lugar.es_local:
        base = _dir_jobs_local(lugar) / jid
        if not base.exists():
            return f"No existe el trabajo '{jid}' en {lugar.nombre}."
        if (base / "codigo").exists():
            return f"El trabajo {jid} ya había terminado (código " \
                   f"{(base / 'codigo').read_text(errors='replace').strip()})."
        try:
            pid = int((base / "pid").read_text().strip())
        except Exception:
            return f"El trabajo {jid} no tiene pid registrado; no se puede matar."
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=15)
        else:
            import signal
            try:
                os.killpg(pid, signal.SIGKILL)
            except Exception:
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
        (base / "codigo").write_text("matado", encoding="utf-8")
        return f"Trabajo {jid} matado (árbol completo, pid {pid})."

    b = f"{_DIR_REMOTO}/{jid}"
    linea = (
        f"b={_q(b)}; "
        f"if [ ! -d \"$b\" ]; then echo \"No existe el trabajo {jid}\"; exit 0; fi; "
        f"if [ -f \"$b/codigo\" ]; then echo \"Ya había terminado (código $(cat \"$b/codigo\"))\"; exit 0; fi; "
        f"pid=$(cat \"$b/pid\" 2>/dev/null); "
        f"if [ -z \"$pid\" ]; then echo 'Sin pid registrado'; exit 0; fi; "
        f"kill -9 -- -\"$pid\" 2>/dev/null || kill -9 \"$pid\" 2>/dev/null; "
        f"echo matado > \"$b/codigo\"; echo \"Trabajo {jid} matado (grupo $pid)\""
    )
    r = T.ejecutar(lugar, linea, timeout=30)
    return r.salida.strip() if r.ok else f"error: {r.error or r.salida}"
