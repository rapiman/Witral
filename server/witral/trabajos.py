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

# Tope del comando 'al_terminar'. Corre DESPUÉS de que el trabajo registró su
# código, así que un webhook colgado nunca deja el trabajo como "corriendo";
# el tope es para que tampoco quede un proceso huérfano para siempre.
_TOPE_AL_TERMINAR = 60

# Alias aceptados para "el trabajo más reciente del lugar".
_ALIAS_ULTIMO = ("ultimo", "último", "last")


# --- Resolver el id -------------------------------------------------------------

def _ultimo_id(lugar: Lugar) -> str:
    """Id del trabajo más reciente del lugar, o "" si no hay ninguno. Los
    directorios se nombran por timestamp (_nuevo_id), así que el más reciente
    es el mayor por nombre."""
    if lugar.es_local:
        raiz = _dir_jobs_local(lugar)
        if not raiz.exists():
            return ""
        nombres = sorted((d.name for d in raiz.iterdir() if d.is_dir()),
                         reverse=True)
        return nombres[0] if nombres else ""
    r = T.ejecutar(lugar, f"ls -1 {_q(_DIR_REMOTO)} 2>/dev/null | sort -r | head -1",
                   timeout=20)
    return (r.salida or "").strip() if r.ok else ""


def resolver_id(lugar: Lugar, jid: str) -> tuple[str, str]:
    """
    (id_real, nota). Con id="ultimo" resuelve al trabajo más reciente del lugar
    —lanzado por run_async o por gradle_build, haya terminado o no— y la nota
    dice a cuál, para que quien llama lo vea y lo pueda acarrear. Con un id
    concreto lo devuelve tal cual. Si no hay trabajos, id_real es "".
    """
    if (jid or "").strip().lower() not in _ALIAS_ULTIMO:
        return jid, ""
    real = _ultimo_id(lugar)
    if not real:
        return "", f"Sin trabajos en {lugar.nombre}: id=\"ultimo\" no tiene a qué resolver."
    return real, f"[id=\"ultimo\" -> {real}]"


def _no_existe(lugar: Lugar, jid: str) -> str:
    """Respuesta ante un id inexistente: trae la lista en la misma respuesta en
    vez de mandar a hacer otra llamada para averiguarla."""
    try:
        lista = listar(lugar, 8)
    except Exception as e:  # la lista es ayuda: si falla, no tapa el mensaje
        lista = f"(no se pudo listar: {e})"
    return (f"No existe el trabajo '{jid}' en {lugar.nombre}. Los ids no se "
            f"pueden anticipar: hay que usar el que devolvió run_async, o "
            f"id=\"ultimo\" para el más reciente.\n{lista}")


# --- Lanzar -------------------------------------------------------------------

def _hook_windows(base: Path) -> str:
    """Contenido de hook.cmd: corre al_terminar.cmd con tope, salida a
    al_terminar.log. El tope va por PowerShell (cmd no tiene un timeout de
    procesos) y el script viaja por -EncodedCommand para no pelear comillas."""
    import base64
    hook, log = base / "al_terminar.cmd", base / "al_terminar.log"
    ps = (f"$p = Start-Process -FilePath cmd.exe -ArgumentList "
          f"'/c \"\"{hook}\" > \"{log}\" 2>&1\"' -PassThru -WindowStyle Hidden; "
          f"if (-not $p.WaitForExit({_TOPE_AL_TERMINAR * 1000})) {{ "
          f"taskkill /T /F /PID $p.Id | Out-Null; "
          f"Add-Content -Path '{log}' -Value 'al_terminar: cortado por el tope "
          f"de {_TOPE_AL_TERMINAR}s' }}")
    b64 = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    return ("@echo off\r\n"
            f"powershell -NoProfile -NonInteractive -EncodedCommand {b64}\r\n")


def _hook_unix(base: str) -> str:
    """Contenido de hook.sh (base relativa o absoluta, ya sin comillas)."""
    s = _q(f"{base}/al_terminar.sh")
    log = _q(f"{base}/al_terminar.log")
    return (f"if command -v timeout >/dev/null 2>&1; then "
            f"timeout {_TOPE_AL_TERMINAR} sh {s} > {log} 2>&1; "
            f"[ $? -eq 124 ] && echo 'al_terminar: cortado por el tope de "
            f"{_TOPE_AL_TERMINAR}s' >> {log}; "
            f"else sh {s} > {log} 2>&1; fi\n")


def lanzar(lugar: Lugar, comando: str, al_terminar: str = "") -> str:
    """
    Lanza 'comando' detached en el lugar. Devuelve el id del trabajo.

    'al_terminar': comando que el wrapper corre al cerrar el trabajo, DESPUÉS de
    registrar su código —también si falló, y si lo mata run_matar—. Recibe
    WITRAL_JOB (id) y WITRAL_CODIGO (código, o 'matado') en el entorno. Su
    salida va a al_terminar.log y NO altera el código del trabajo. Tope
    _TOPE_AL_TERMINAR s. Es el único aviso real de fin: lo emite la máquina que
    terminó, no Witral (que no puede empujar nada al cliente MCP).
    """
    jid = _nuevo_id()
    if lugar.es_local:
        base = _dir_jobs_local(lugar) / jid
        base.mkdir(parents=True, exist_ok=True)
        (base / "cmd.txt").write_text(comando, encoding="utf-8")
        if al_terminar:
            (base / "al_terminar.txt").write_text(al_terminar, encoding="utf-8")
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
            # %errorlevel% se captura en WITRAL_CODIGO en la MISMA línea en que
            # se expande, antes de que otra instrucción lo pise; el código se
            # registra primero y recién después corre al_terminar.
            cuerpo = ("@echo off\r\n"
                      f"set WITRAL_JOB={jid}\r\n"
                      f"call \"{interno}\" > \"{out}\" 2> \"{err}\"\r\n"
                      "set WITRAL_CODIGO=%errorlevel%\r\n"
                      f"echo %WITRAL_CODIGO% > \"{cod}\"\r\n")
            if al_terminar:
                (base / "al_terminar.cmd").write_text(
                    f"@echo off\r\n@chcp 65001 >nul\r\n{al_terminar}\r\n",
                    encoding="utf-8")
                (base / "hook.cmd").write_text(_hook_windows(base),
                                               encoding="ascii")
                cuerpo += f"call \"{base / 'hook.cmd'}\"\r\n"
            bat.write_text(cuerpo, encoding="utf-8")
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
            linea = (f"export WITRAL_JOB={jid}; "
                     f"({comando}) > {_q(str(out))} 2> {_q(str(err))}; "
                     f"WITRAL_CODIGO=$?; export WITRAL_CODIGO; "
                     f"echo $WITRAL_CODIGO > {_q(str(cod))}")
            if al_terminar:
                (base / "al_terminar.sh").write_text(al_terminar + "\n",
                                                     encoding="utf-8")
                (base / "hook.sh").write_text(_hook_unix(str(base)),
                                              encoding="utf-8")
                linea += f"; sh {_q(str(base / 'hook.sh'))}"
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
    interno = (f"echo $$ > {base}/pid; export WITRAL_JOB={jid}; "
               f"( {cd}( {comando} ) ) > {base}/out.log 2> {base}/err.log; "
               f"WITRAL_CODIGO=$?; export WITRAL_CODIGO; "
               f"echo $WITRAL_CODIGO > {base}/codigo")
    previo = ""
    if al_terminar:
        # Los archivos del hook se escriben ANTES de lanzar, con printf (sin
        # heredocs), para que el wrapper solo tenga que invocarlos.
        # El hook corre desde el HOME (ahí viven las rutas del job); el cd va
        # dentro del script para que al_terminar tenga el mismo cwd que el
        # comando: la raíz del lugar.
        guion = (f"cd {_q(lugar.raiz)} || exit 1\n" if lugar.raiz else "") + al_terminar
        previo = (f"printf '%s\\n' {_q(guion)} > {_q(base + '/al_terminar.sh')}; "
                  f"printf %s {_q(al_terminar)} > {_q(base + '/al_terminar.txt')}; "
                  f"printf %s {_q(_hook_unix(base))} > {_q(base + '/hook.sh')}; ")
        interno += f"; sh {base}/hook.sh"
    linea = (f"mkdir -p {_q(base)} && printf %s {_q(comando)} > {_q(base + '/cmd.txt')}; "
             f"{previo}"
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


def _tramo(texto: str, desde: int, lineas: int) -> tuple[list[str], int, str]:
    """
    Qué mostrar de un log. Devuelve (líneas_a_mostrar, completas, rótulo).

    Sin 'desde' (0): las últimas 'lineas', como siempre. Con 'desde'=N: solo lo
    que vino DESPUÉS de la línea N —el delta—, para no traer el mismo tail en
    cada llamada. 'completas' cuenta solo las líneas terminadas en salto (igual
    que wc -l): una última línea a medio escribir se muestra pero no se cuenta,
    así la próxima llamada con desde=completas la vuelve a traer entera.
    """
    trozos = texto.split("\n")
    completas = len(trozos) - 1
    todas = [t.rstrip("\r") for t in trozos]
    if todas and todas[-1] == "":
        todas.pop()
    total = len(todas)
    if total == 0:
        return [], completas, "vacío"
    if desde <= 0:
        return todas[-lineas:], completas, f"últimas {min(lineas, total)} de {total} líneas"
    if desde > completas:
        return (todas[-lineas:], completas,
                f"desde={desde} pasa el total de {completas} líneas (¿log de "
                f"otro trabajo?); últimas {min(lineas, total)}")
    nuevas = todas[desde:]
    if not nuevas:
        return [], completas, f"sin líneas nuevas después de la {desde}"
    omitidas = max(0, len(nuevas) - lineas)
    rotulo = f"nuevas: líneas {desde + 1}-{total}"
    if omitidas:
        rotulo += f"; se omiten las {omitidas} primeras, se muestran las últimas {lineas}"
    return nuevas[-lineas:], completas, rotulo


def _pie_delta(n_out: int, n_err: int) -> str:
    return (f"[delta: para traer solo lo nuevo en la próxima llamada, "
            f"desde_out={n_out}, desde_err={n_err}]")


def _al_terminar_local(base) -> list[str]:
    """Líneas de estado del comando al_terminar, si el trabajo tiene uno."""
    ruta = base / "al_terminar.txt"
    if not ruta.exists():
        return []
    partes = ["al_terminar: " + ruta.read_text(encoding="utf-8",
                                                errors="replace").strip()]
    log = base / "al_terminar.log"
    if log.exists():
        cola = _cola_texto(log.read_text(encoding="utf-8", errors="replace"), 10)
        partes.append(f"--- al_terminar.log ---\n{cola}" if cola
                      else "--- al_terminar.log --- (vacío)")
    else:
        partes.append("(al_terminar todavía no corrió)")
    return partes


def estado(lugar: Lugar, jid: str, lineas: int = 40,
           desde_out: int = 0, desde_err: int = 0) -> str:
    """Estado + salida de un trabajo: las últimas 'lineas', o solo lo nuevo
    después de 'desde_out'/'desde_err'. Acepta id="ultimo"."""
    jid, nota = resolver_id(lugar, jid)
    if not jid:
        return nota
    if lugar.es_local:
        base = _dir_jobs_local(lugar) / jid
        if not base.exists():
            return _no_existe(lugar, jid)
        partes = [nota] if nota else []
        partes.append(f"Trabajo {jid} en {lugar.nombre}")
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
        cuentas = {"out.log": 0, "err.log": 0}
        for nombre, desde in (("out.log", desde_out), ("err.log", desde_err)):
            ruta = base / nombre
            if ruta.exists():
                txt = ruta.read_text(encoding="utf-8", errors="replace")
                mostrar, cuentas[nombre], rotulo = _tramo(txt, desde, lineas)
                partes.append(f"--- {nombre} ({rotulo}) ---"
                              + ("\n" + "\n".join(mostrar) if mostrar else ""))
        partes += _al_terminar_local(base)
        partes.append(_pie_delta(cuentas["out.log"], cuentas["err.log"]))
        return "\n".join(partes)

    b = f"{_DIR_REMOTO}/{jid}"

    def _log_remoto(nombre: str, desde: int) -> str:
        # wc -l cuenta saltos de línea: mismo criterio que _tramo en local.
        return (
            f"f=\"$b/{nombre}\"; n=0; [ -f \"$f\" ] && n=$(wc -l < \"$f\" | tr -d ' '); "
            f"echo \"__WITRAL_N {nombre} $n\"; "
            f"if [ {int(desde)} -gt 0 ] && [ {int(desde)} -le \"$n\" ]; then "
            f"echo \"--- {nombre} (nuevas: desde la línea {int(desde) + 1}, "
            f"a lo sumo {lineas}) ---\"; "
            f"tail -n +{int(desde) + 1} \"$f\" | tail -n {lineas}; "
            f"else "
            f"if [ {int(desde)} -gt \"$n\" ]; then echo \"(desde={int(desde)} pasa el "
            f"total de $n líneas)\"; fi; "
            f"echo \"--- {nombre} (últimas {lineas} de $n) ---\"; "
            f"tail -n {lineas} \"$f\" 2>/dev/null; fi; "
        )

    linea = (
        f"b={_q(b)}; "
        f"if [ ! -d \"$b\" ]; then echo \"__WITRAL_NO_EXISTE\"; exit 0; fi; "
        f"echo \"Trabajo {jid} en {lugar.nombre}\"; "
        f"echo \"cmd: $(cat \"$b/cmd.txt\" 2>/dev/null)\"; "
        f"if [ -f \"$b/codigo\" ]; then echo \"estado: TERMINADO, código $(cat \"$b/codigo\")\"; "
        f"else pid=$(cat \"$b/pid\" 2>/dev/null); "
        f"if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then "
        f"echo \"estado: CORRIENDO (pid $pid)\"; "
        f"else echo \"estado: TERMINADO sin código — el proceso ya no existe "
        f"(abortado, o el wrapper murió antes de registrarlo)\"; fi; fi; "
        + _log_remoto("out.log", desde_out)
        + _log_remoto("err.log", desde_err)
        + f"if [ -f \"$b/al_terminar.txt\" ]; then "
        f"echo \"al_terminar: $(cat \"$b/al_terminar.txt\")\"; "
        f"if [ -f \"$b/al_terminar.log\" ]; then echo '--- al_terminar.log ---'; "
        f"tail -n 10 \"$b/al_terminar.log\"; "
        f"else echo '(al_terminar todavía no corrió)'; fi; fi"
    )
    r = T.ejecutar(lugar, linea, timeout=30)
    if not r.ok:
        return f"error: {r.error or r.salida}"
    if "__WITRAL_NO_EXISTE" in r.salida:
        return _no_existe(lugar, jid)
    cuentas = {"out.log": 0, "err.log": 0}
    visibles = []
    for l in r.salida.splitlines():
        if l.startswith("__WITRAL_N "):
            _, nombre, n = (l.split() + ["0"])[:3]
            cuentas[nombre] = int(n) if n.isdigit() else 0
        else:
            visibles.append(l)
    partes = ([nota] if nota else []) + visibles
    partes.append(_pie_delta(cuentas["out.log"], cuentas["err.log"]))
    return "\n".join(partes)


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
            lineas: int = 40, hasta_patron: str = "",
            desde_out: int = 0, desde_err: int = 0) -> str:
    """
    Bloquea del lado de Witral hasta que el trabajo termine, y devuelve su
    estado final. Evita el polling manual con sleep+run_status.

    Como el cliente MCP corta las llamadas largas, cada llamada espera a lo
    sumo _TOPE_ESPERA s: si el trabajo termina antes, vuelve al instante; si
    sigue corriendo al llegar al tope, devuelve el estado parcial e indica
    volver a llamar. 'hasta_segundos' es el techo que pide el usuario, pero se
    acota a _TOPE_ESPERA por llamada. Acepta id="ultimo"; 'desde_out' y
    'desde_err' traen solo las líneas nuevas (ver _tramo).
    """
    jid, nota = resolver_id(lugar, jid)
    if not jid:
        return nota
    pre = nota + "\n" if nota else ""

    def _estado() -> str:
        # resolver_id ya resolvió: se pasa el id real para no repetir la nota.
        return estado(lugar, jid, lineas, desde_out, desde_err)

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
                return (pre + f"[run_esperar: MATCH de /{hasta_patron}/ tras "
                        f"~{int(time.time() - t0)}s]\n{linea}\n\n" + _estado())
        est = _estado_rapido(lugar, jid)
        if est == "no_existe":
            return _no_existe(lugar, jid)
        if est in ("terminado", "terminado_sin_codigo"):
            # TERMINAL: se devuelve el estado y NUNCA el pie de "volver a
            # llamar". Que el pie sea inalcanzable desde aquí es justamente el
            # arreglo: antes se decidía por reloj, sin mirar este estado.
            final = pre + _estado()
            if rx is not None:
                final += (f"\n\n[run_esperar: el trabajo TERMINÓ sin que "
                          f"apareciera /{hasta_patron}/ en los logs.]")
            return final
        transcurrido = time.time() - t0
        if transcurrido >= presupuesto:
            parcial = pre + _estado()
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
                    f"{jid}\", donde=\"{lugar.nombre}\") con los desde_out/"
                    f"desde_err del pie [delta] para no recibir de nuevo lo ya "
                    f"visto.{sugerencia} Witral no puede avisar solo al "
                    f"terminar; run_async(..., al_terminar=...) sí, porque el "
                    f"aviso lo emite la máquina que terminó.]")
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

def _hook_tras_matar_local(lugar: Lugar, base, jid: str) -> str:
    """run_matar mata el árbol, wrapper incluido, así que la línea que llamaba
    a al_terminar ya no va a correr: se lanza desde aquí, detached, con
    WITRAL_CODIGO=matado. Devuelve el texto a agregar a la respuesta."""
    hook = base / ("hook.cmd" if os.name == "nt" else "hook.sh")
    if not hook.exists():
        return ""
    entorno = dict(os.environ, WITRAL_JOB=jid, WITRAL_CODIGO="matado")
    try:
        if os.name == "nt":
            flags = (subprocess.CREATE_NEW_PROCESS_GROUP
                     | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
            subprocess.Popen(["cmd", "/c", str(hook)], cwd=lugar.raiz,
                             env=entorno, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=flags)
        else:
            subprocess.Popen(["sh", str(hook)], cwd=lugar.raiz,
                             env=entorno, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    except OSError as e:
        return f"\nal_terminar NO se pudo lanzar: {e}"
    return "\nal_terminar lanzado (WITRAL_CODIGO=matado)."


def matar(lugar: Lugar, jid: str) -> str:
    """Mata el ÁRBOL de procesos del trabajo y marca su código como 'matado'."""
    if lugar.es_local:
        base = _dir_jobs_local(lugar) / jid
        if not base.exists():
            return (f"No existe el trabajo '{jid}' en {lugar.nombre} "
                    f"(el registro se perdió o expiró). Si el proceso sigue "
                    f"vivo, ubicar su pid con procesos() y matarlo con "
                    f"run_matar(pid=...).")
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
        return (f"Trabajo {jid} matado (árbol completo, pid {pid})."
                + _hook_tras_matar_local(lugar, base, jid))

    b = f"{_DIR_REMOTO}/{jid}"
    linea = (
        f"b={_q(b)}; "
        f"if [ ! -d \"$b\" ]; then echo \"No existe el trabajo {jid} (registro perdido); si el proceso sigue vivo, matar con run_matar(pid=...)\"; exit 0; fi; "
        f"if [ -f \"$b/codigo\" ]; then echo \"Ya había terminado (código $(cat \"$b/codigo\"))\"; exit 0; fi; "
        f"pid=$(cat \"$b/pid\" 2>/dev/null); "
        f"if [ -z \"$pid\" ]; then echo 'Sin pid registrado'; exit 0; fi; "
        f"kill -9 -- -\"$pid\" 2>/dev/null || kill -9 \"$pid\" 2>/dev/null; "
        f"echo matado > \"$b/codigo\"; echo \"Trabajo {jid} matado (grupo $pid)\"; "
        # El wrapper murió con el árbol: al_terminar se dispara desde aquí,
        # detached, con WITRAL_CODIGO=matado.
        f"if [ -f \"$b/hook.sh\" ]; then "
        f"WITRAL_JOB={jid} WITRAL_CODIGO=matado setsid sh \"$b/hook.sh\" "
        f"< /dev/null > /dev/null 2>&1 & "
        f"echo \"al_terminar lanzado (WITRAL_CODIGO=matado).\"; fi"
    )
    r = T.ejecutar(lugar, linea, timeout=30)
    return r.salida.strip() if r.ok else f"error: {r.error or r.salida}"


# --- Por PID (procesos huérfanos sin registro de trabajo) --------------------

def estado_pid(lugar: Lugar, pid: int) -> str:
    """Dice si 'pid' sigue vivo. Para procesos cuyo registro de trabajo se
    perdió (server local que quedó fuera de .witral/jobs)."""
    if lugar.es_local:
        vivo = _pid_vivo_local(pid)
        return (f"El pid {pid} está VIVO en {lugar.nombre}."
                if vivo else f"El pid {pid} NO está vivo en {lugar.nombre}.")
    linea = (f"if kill -0 {pid} 2>/dev/null; then "
             f"echo \"El pid {pid} está VIVO en {lugar.nombre}.\"; "
             f"else echo \"El pid {pid} NO está vivo en {lugar.nombre}.\"; fi")
    r = T.ejecutar(lugar, linea, timeout=30)
    return r.salida.strip() if r.ok else f"error: {r.error or r.salida}"


def matar_pid(lugar: Lugar, pid: int) -> str:
    """Mata el árbol de 'pid' sin necesitar registro de trabajo. Para el caso
    del server local que sigue vivo aunque su job_id ya no exista."""
    if lugar.es_local:
        if os.name == "nt":
            r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=15)
            if r.returncode != 0:
                detalle = (r.stderr or r.stdout or b"").decode(
                    "utf-8", errors="replace").strip()
                return (f"No se pudo matar el pid {pid}. taskkill: "
                        f"{detalle or 'sin salida'}")
        else:
            import signal
            try:
                os.killpg(pid, signal.SIGKILL)
            except Exception:
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    return (f"No se pudo matar el pid {pid} (no existe o ya "
                            f"no responde).")
        return f"Pid {pid} matado (árbol completo) en {lugar.nombre}."
    linea = (f"kill -9 -- -{pid} 2>/dev/null || kill -9 {pid} 2>/dev/null; "
             f"echo \"Pid {pid} matado en {lugar.nombre}.\"")
    r = T.ejecutar(lugar, linea, timeout=30)
    return r.salida.strip() if r.ok else f"error: {r.error or r.salida}"
