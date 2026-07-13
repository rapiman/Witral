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


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


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
            bat = base / "lanzar.cmd"
            bat.write_text(
                "@echo off\r\n"
                "@chcp 65001 >nul\r\n"
                f"(\r\n{comando}\r\n) > \"{out}\" 2> \"{err}\"\r\n"
                f"echo %errorlevel% > \"{cod}\"\r\n",
                encoding="utf-8",
            )
            # CREATE_NO_WINDOW (consola OCULTA propia) y NO DETACHED_PROCESS:
            # son excluyentes, y sin consola las console-apps (ping, timeout,
            # el host de powershell) corren mudas o mueren al instante.
            # Verificado con A/B: DETACHED => out vacío; NO_WINDOW => captura OK.
            flags = (subprocess.CREATE_NEW_PROCESS_GROUP
                     | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
            proc = subprocess.Popen(
                ["cmd", "/c", str(bat)], cwd=lugar.raiz,
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
        cod = None
        if (base / "codigo").exists():
            cod = (base / "codigo").read_text(encoding="utf-8", errors="replace").strip()
        if cod is not None:
            partes.append(f"estado: TERMINADO, código {cod}")
        else:
            pid = None
            try:
                pid = int((base / "pid").read_text().strip())
            except Exception:
                pass
            if pid and _pid_vivo_local(pid):
                partes.append(f"estado: CORRIENDO (pid {pid})")
            else:
                partes.append("estado: sin código y proceso no encontrado "
                              "(¿abortado o recién lanzado?)")
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
        f"else echo \"estado: sin código y proceso no encontrado\"; fi; fi; "
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
        base = _dir_jobs_local(lugar) / jid
        if not base.exists():
            return "no_existe"
        return "terminado" if (base / "codigo").exists() else "corriendo"
    b = f"{_DIR_REMOTO}/{jid}"
    linea = (f"if [ ! -d {_q(b)} ]; then echo no_existe; "
             f"elif [ -f {_q(b)}/codigo ]; then echo terminado; "
             f"else echo corriendo; fi")
    r = T.ejecutar(lugar, linea, timeout=20)
    est = (r.salida or "").strip()
    return est if est in ("no_existe", "terminado", "corriendo") else "corriendo"


def esperar(lugar: Lugar, jid: str, hasta_segundos: int = 600,
            lineas: int = 40) -> str:
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
    t0 = time.time()
    while True:
        est = _estado_rapido(lugar, jid)
        if est == "no_existe":
            return (f"No existe el trabajo '{jid}' en {lugar.nombre}. "
                    f"Ver run_status sin id.")
        if est == "terminado":
            return estado(lugar, jid, lineas)
        transcurrido = time.time() - t0
        if transcurrido >= presupuesto:
            parcial = estado(lugar, jid, lineas)
            return (parcial + f"\n\n[run_esperar: sigue CORRIENDO tras "
                    f"~{int(transcurrido)}s. El cliente MCP corta las llamadas "
                    f"largas, por eso la espera se topa en ~{_TOPE_ESPERA}s. "
                    f"Volvé a llamar run_esperar(id=\"{jid}\", donde=\""
                    f"{lugar.nombre}\") para seguir esperando.]")
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
            if (d / "codigo").exists():
                est = "terminado(" + (d / "codigo").read_text(errors="replace").strip() + ")"
            else:
                est = "corriendo?"
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
