"""
Transporte: cómo viaja una operación a su lugar.

- Local: se ejecuta directo (subprocess / acceso a disco).
- Remoto: vía SSH (comandos) y SFTP (archivos), usando paramiko.

Las conexiones SSH se abren una vez por lugar y se reutilizan (cache simple
por nombre de lugar), reflejando "el destino se resuelve primero y la sesión
se reutiliza".

paramiko es opcional en import: si no está instalado, las operaciones locales
siguen funcionando y solo fallan las remotas, con un mensaje claro.
"""

from __future__ import annotations

import subprocess
import os as _os
import time as _time
import uuid as _uuid
import tempfile as _tempfile
from dataclasses import dataclass

from .config import Lugar, SSHConfig

try:
    import paramiko  # type: ignore
    _HAY_PARAMIKO = True
except Exception:  # pragma: no cover
    paramiko = None  # type: ignore
    _HAY_PARAMIKO = False


@dataclass
class Resultado:
    """Resultado uniforme de ejecutar un comando, local o remoto."""
    codigo: int
    salida: str
    error: str

    @property
    def ok(self) -> bool:
        return self.codigo == 0


class TransporteError(Exception):
    pass


# --- Cache de clientes SSH por nombre de lugar -----------------------------

_clientes: dict[str, "paramiko.SSHClient"] = {}


def _cliente_ssh(lugar: Lugar) -> "paramiko.SSHClient":
    if not _HAY_PARAMIKO:
        raise TransporteError(
            "paramiko no está instalado; no se puede operar en remoto. "
            "Instalar con: pip install paramiko"
        )
    if lugar.nombre in _clientes:
        cli = _clientes[lugar.nombre]
        # Verificar que el transporte siga vivo.
        tr = cli.get_transport()
        if tr is not None and tr.is_active():
            return cli
        _clientes.pop(lugar.nombre, None)

    ssh: SSHConfig = lugar.requiere_ssh()
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(
        hostname=ssh.host,
        port=ssh.puerto,
        username=ssh.usuario,
        key_filename=ssh.clave,
        password=ssh.password,
        passphrase=ssh.passphrase,
        timeout=15,
        allow_agent=True,
        look_for_keys=ssh.clave is None,
    )
    _clientes[lugar.nombre] = cli
    return cli


def cerrar_todo() -> None:
    for cli in _clientes.values():
        try:
            cli.close()
        except Exception:
            pass
    _clientes.clear()


# --- Ejecución de comandos --------------------------------------------------

def ejecutar(lugar: Lugar, argv: list[str] | str, *, cwd: str | None = None,
             entrada: str | None = None, timeout: int = 120) -> Resultado:
    """
    Ejecuta un comando en el lugar. En local usa subprocess (lista de args =
    sin shell; string = con shell). En remoto manda la línea por SSH.
    """
    if lugar.es_local:
        return _ejecutar_local(argv, cwd=cwd, entrada=entrada, timeout=timeout)
    return _ejecutar_remoto(lugar, argv, cwd=cwd, entrada=entrada, timeout=timeout)


def _ejecutar_local(argv, *, cwd, entrada, timeout) -> Resultado:
    usar_shell = isinstance(argv, str)
    import os as _os
    import tempfile as _tempfile
    env = _os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    # Asegurar TMP/TEMP válidos: el entorno heredado del cliente MCP puede
    # traerlos sin definir o con valores rotos (p. ej. "%TMP%" literal). La JVM
    # los necesita para crear el socket de su selector NIO; sin esto, Gradle/
    # Java fallan con "Unable to establish loopback connection".
    tmp_ok = _tempfile.gettempdir()
    for var in ("TMP", "TEMP"):
        valor = env.get(var, "")
        if (not valor) or ("%" in valor) or (not _os.path.isdir(valor.strip())):
            env[var] = tmp_ok
        else:
            env[var] = valor.strip()
    kwargs = dict(
        shell=usar_shell,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if entrada is not None:
        kwargs["input"] = entrada
    else:
        # stdin como pipe vacío (EOF inmediato), NO DEVNULL: evita que git
        # cuelgue esperando entrada, pero le da a la JVM/Gradle un handle de
        # stdin válido para su selector NIO (DEVNULL rompe el loopback en Windows).
        kwargs["input"] = ""
    try:
        proc = subprocess.run(argv, **kwargs)
    except subprocess.TimeoutExpired as e:
        return Resultado(124, e.stdout or "", f"timeout tras {timeout}s")
    return Resultado(proc.returncode, proc.stdout, proc.stderr)


def _sch(args):
    return subprocess.run(["schtasks", *args], capture_output=True,
                          text=True, timeout=30)


def _tarea_base(nombre: str) -> str:
    return _os.path.join(_tempfile.gettempdir(), nombre)


def tarea_lanzar(comando: str, *, cwd: str | None = None) -> str:
    """
    Lanza un comando como tarea programada de Windows (fuera del sandbox del
    cliente MCP) y RETORNA DE INMEDIATO el nombre de la tarea. No espera a que
    termine. Para procesos que necesitan sockets loopback (Gradle/Java),
    bloqueados dentro del aislamiento de Claude Desktop. Solo Windows.

    El .bat escribe la salida a <base>.out y, al terminar, el código de salida
    a <base>.done. Esos archivos los lee tarea_consultar.
    """
    nombre = "witral_" + _uuid.uuid4().hex[:12]
    base = _tarea_base(nombre)
    salida, script, done = base + ".out", base + ".bat", base + ".done"

    cd = f'cd /d "{cwd}"\r\n' if cwd else ""
    with open(script, "w", encoding="utf-8") as f:
        f.write("@echo off\r\n")
        f.write(cd)
        f.write(f'{comando} > "{salida}" 2>&1\r\n')
        f.write(f'echo %ERRORLEVEL% > "{done}"\r\n')

    inner = f'cmd /c "{script}"'
    # /st futuro: schtasks rechaza una hora pasada. /run la dispara ya igual.
    futuro = _time.strftime("%H:%M", _time.localtime(_time.time() + 120))

    crear = _sch(["/create", "/tn", nombre, "/tr", inner,
                  "/sc", "once", "/st", futuro, "/f"])
    if crear.returncode != 0:
        raise TransporteError("No se pudo crear la tarea: " + crear.stderr)
    run = _sch(["/run", "/tn", nombre])
    if run.returncode != 0:
        _sch(["/delete", "/tn", nombre, "/f"])
        raise TransporteError("No se pudo lanzar la tarea: " + run.stderr)
    return nombre


def tarea_consultar(nombre: str) -> dict:
    """
    Estado de una tarea lanzada con tarea_lanzar. Devuelve un dict con:
      terminada (bool), codigo (int|None), salida (str), existe (bool).
    'terminada' se basa en el archivo .done (fiable), no en el estado de schtasks.
    """
    base = _tarea_base(nombre)
    salida, done = base + ".out", base + ".done"

    q = _sch(["/query", "/tn", nombre, "/v", "/fo", "list"])
    existe = q.returncode == 0

    codigo = None
    terminada = _os.path.exists(done)
    if terminada:
        try:
            with open(done) as f:
                codigo = int((f.read().strip() or "0"))
        except (ValueError, OSError):
            codigo = 0

    texto = ""
    try:
        with open(salida, "r", encoding="utf-8", errors="replace") as f:
            texto = f.read()
    except FileNotFoundError:
        pass

    return {"existe": existe, "terminada": terminada,
            "codigo": codigo, "salida": texto}


def tarea_detener(nombre: str) -> bool:
    """Detiene la ejecución en curso de la tarea (schtasks /end)."""
    r = _sch(["/end", "/tn", nombre])
    return r.returncode == 0


def tarea_eliminar(nombre: str) -> bool:
    """Borra la tarea y limpia sus archivos temporales (.out/.bat/.done)."""
    _sch(["/end", "/tn", nombre])
    r = _sch(["/delete", "/tn", nombre, "/f"])
    base = _tarea_base(nombre)
    for ext in (".out", ".bat", ".done"):
        try:
            _os.remove(base + ext)
        except OSError:
            pass
    return r.returncode == 0


def _quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _ejecutar_remoto(lugar, argv, *, cwd, entrada, timeout) -> Resultado:
    cli = _cliente_ssh(lugar)
    if isinstance(argv, list):
        linea = " ".join(_quote(a) for a in argv)
    else:
        linea = argv
    if cwd:
        linea = f"cd {_quote(cwd)} && {linea}"
    stdin, stdout, stderr = cli.exec_command(linea, timeout=timeout)
    if entrada:
        stdin.write(entrada)
        stdin.channel.shutdown_write()
    salida = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace")
    codigo = stdout.channel.recv_exit_status()
    return Resultado(codigo, salida, error)


# --- Transferencia de archivos (SFTP) --------------------------------------

def subir(lugar: Lugar, local_path: str, remoto_path: str) -> None:
    cli = _cliente_ssh(lugar)
    sftp = cli.open_sftp()
    try:
        sftp.put(local_path, remoto_path)
    finally:
        sftp.close()


def bajar(lugar: Lugar, remoto_path: str, local_path: str) -> None:
    cli = _cliente_ssh(lugar)
    sftp = cli.open_sftp()
    try:
        sftp.get(remoto_path, local_path)
    finally:
        sftp.close()


# --- Lectura/escritura de archivos remotos vía SFTP ------------------------

def leer_remoto(lugar: Lugar, ruta: str) -> bytes:
    cli = _cliente_ssh(lugar)
    sftp = cli.open_sftp()
    try:
        with sftp.open(ruta, "rb") as f:
            return f.read()
    finally:
        sftp.close()


def escribir_remoto(lugar: Lugar, ruta: str, contenido: bytes) -> None:
    cli = _cliente_ssh(lugar)
    sftp = cli.open_sftp()
    try:
        with sftp.open(ruta, "wb") as f:
            f.write(contenido)
    finally:
        sftp.close()
