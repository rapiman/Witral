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

    # TOFU (Trust On First Use) con known_hosts propio: la primera conexión a
    # un host guarda su clave en ~/.witral/known_hosts; si en una conexión
    # posterior la clave cambió, paramiko lanza BadHostKeyException (posible
    # MITM o server reinstalado) en vez de aceptarla en silencio.
    ruta_kh = _ruta_known_hosts()
    if ruta_kh.exists():
        try:
            cli.load_host_keys(str(ruta_kh))
        except Exception:
            pass  # archivo corrupto => se regenera al guardar

    class _TOFU(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            client.get_host_keys().add(hostname, key.get_name(), key)
            try:
                client.save_host_keys(str(ruta_kh))
            except Exception:
                pass  # no poder persistir no impide conectar

    cli.set_missing_host_key_policy(_TOFU())
    destino = f"{ssh.usuario}@{ssh.host}:{ssh.puerto}"
    try:
        cli.connect(
            hostname=ssh.host,
            port=ssh.puerto,
            username=ssh.usuario,
            key_filename=ssh.clave,
            password=ssh.password,
            passphrase=ssh.passphrase,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
            allow_agent=True,
            # Solo buscar claves por defecto si no hay ni clave ni password
            # configurados (evita intentos de más en servidores estrictos).
            look_for_keys=(ssh.clave is None and ssh.password is None),
        )
    except Exception as e:
        raise TransporteError(_diagnostico_ssh(e, destino)) from e
    _clientes[lugar.nombre] = cli
    return cli


def _diagnostico_ssh(e: Exception, destino: str) -> str:
    """Traduce el fallo de conexión SSH a un mensaje claro según su causa."""
    import socket
    nombre = type(e).__name__
    if _HAY_PARAMIKO and isinstance(e, paramiko.BadHostKeyException):
        return (f"SSH a {destino}: LA CLAVE DEL HOST CAMBIÓ respecto de la "
                f"guardada en ~/.witral/known_hosts (posible MITM, o server "
                f"reinstalado). Si el cambio es legítimo, borrar la línea de "
                f"ese host en el archivo y reconectar.")
    if _HAY_PARAMIKO and isinstance(e, paramiko.AuthenticationException):
        return (f"SSH a {destino}: autenticación rechazada. Revisá usuario, "
                f"clave o password en lugares.json.")
    if isinstance(e, socket.gaierror):
        return (f"SSH a {destino}: el host no resuelve (DNS). Revisá el nombre "
                f"del host o tu conexión/VPN.")
    if isinstance(e, socket.timeout) or "timed out" in str(e).lower():
        return (f"SSH a {destino}: timeout de conexión (15s). El host no "
                f"responde: puede estar caído, bloqueado por firewall, o "
                f"necesitás VPN.")
    if isinstance(e, ConnectionRefusedError) or "refused" in str(e).lower():
        return (f"SSH a {destino}: conexión rechazada. ¿El puerto es correcto y "
                f"el servicio SSH está corriendo?")
    return f"SSH a {destino}: fallo de conexión ({nombre}: {e})."


def _ruta_known_hosts():
    """Ruta del known_hosts propio de Witral (~/.witral/known_hosts)."""
    from pathlib import Path
    d = Path.home() / ".witral"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d / "known_hosts"


def cerrar_todo() -> None:
    for cli in _clientes.values():
        try:
            cli.close()
        except Exception:
            pass
    _clientes.clear()


# --- Ejecución de comandos --------------------------------------------------

def ejecutar(lugar: Lugar, argv: list[str] | str, *, cwd: str | None = None,
             entrada: str | None = None, timeout: int = 120,
             env_extra: dict[str, str] | None = None) -> Resultado:
    """
    Ejecuta un comando en el lugar. En local usa subprocess (lista de args =
    sin shell; string = con shell). En remoto manda la línea por SSH.
    'env_extra': variables de entorno adicionales; en remoto se prefijan
    como asignaciones VAR=valor en la línea (shell POSIX).
    """
    if lugar.es_local:
        return _ejecutar_local(argv, cwd=cwd, entrada=entrada, timeout=timeout,
                               env_extra=env_extra)
    return _ejecutar_remoto(lugar, argv, cwd=cwd, entrada=entrada,
                            timeout=timeout, env_extra=env_extra)


def _decodificar_salida(data: bytes) -> str:
    """
    Decodifica la salida de un subproceso: UTF-8 primero (git y las
    herramientas modernas emiten UTF-8; con text=True Python usaba la ANSI
    de Windows y aparecía mojibake tipo 'MigraciÃ³n'). Si no es UTF-8
    válido, cae al codepage OEM de la consola (tasklist, sc, etc.).
    """
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if _os.name == "nt":
        try:
            import ctypes
            oem = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        except Exception:
            oem = "cp850"
        return data.decode(oem, "replace")
    return data.decode("latin-1", "replace")


def _matar_arbol(proc) -> None:
    """
    Mata el ÁRBOL de procesos de 'proc', no solo el hijo directo. Con
    shell=True el hijo es cmd.exe/sh y el comando real es un NIETO:
    proc.kill() deja al nieto vivo sujetando los pipes, y el drenaje
    posterior se cuelga para siempre. taskkill /T /F en Windows;
    killpg en unix (requiere start_new_session=True al lanzar).
    """
    try:
        if _os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            import signal
            _os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _ejecutar_local(argv, *, cwd, entrada, timeout, env_extra=None) -> Resultado:
    usar_shell = isinstance(argv, str)
    import os as _os
    import tempfile as _tempfile
    env = _os.environ.copy()
    # Nunca esperar credenciales interactivas: mejor fallar al instante con
    # mensaje claro que colgarse en un prompt que nadie va a responder.
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GCM_INTERACTIVE", "never")
    if env_extra:
        env.update(env_extra)
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
    popen_kwargs = dict(
        shell=usar_shell,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if _os.name != "nt":
        # Grupo de procesos propio: permite matar el árbol completo (killpg).
        popen_kwargs["start_new_session"] = True
    # Modo bytes (sin text=True): la decodificación la hace _decodificar_salida
    # (UTF-8 primero) para evitar el mojibake de la codepage ANSI de Windows.
    # stdin como pipe con EOF inmediato (b"" si no hay entrada), NO DEVNULL:
    # evita que git cuelgue esperando entrada, pero le da a la JVM/Gradle un
    # handle de stdin válido para su selector NIO (DEVNULL rompe el loopback
    # en Windows).
    datos = entrada.encode("utf-8") if entrada is not None else b""
    try:
        proc = subprocess.Popen(argv, **popen_kwargs)
    except (OSError, ValueError) as e:
        return Resultado(127, "", f"no se pudo lanzar el comando: {e}")
    try:
        out, err = proc.communicate(input=datos, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Con subprocess.run, tras el timeout se mataba SOLO al hijo directo y
        # el drenaje de pipes quedaba colgado para siempre si un nieto seguía
        # vivo (los cuelgues de 4 min que ni siquiera devolvían 124). Ahora se
        # mata el árbol entero y el drenaje termina.
        _matar_arbol(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = b"", b""
            for canal in (proc.stdout, proc.stderr, proc.stdin):
                try:
                    if canal:
                        canal.close()
                except Exception:
                    pass
        detalle = _decodificar_salida(err or b"").strip()
        msj = f"timeout tras {timeout}s (árbol de procesos terminado)"
        return Resultado(124, _decodificar_salida(out or b""),
                         f"{detalle}\n{msj}".strip() if detalle else msj)
    return Resultado(proc.returncode, _decodificar_salida(out),
                     _decodificar_salida(err))



def _quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _es_error_conexion(e: Exception) -> bool:
    """¿La excepción delata una conexión muerta/reseteada (no un error lógico)?"""
    if isinstance(e, (EOFError, ConnectionResetError, BrokenPipeError)):
        return True
    txt = str(e).lower()
    return any(s in txt for s in (
        "10054", "forcibly closed", "reset by peer", "connection reset",
        "session not active", "not active", "broken pipe", "socket is closed",
    ))


def _descartar_cliente(lugar: Lugar) -> None:
    """Saca el cliente del cache y lo cierra, para forzar reconexión limpia."""
    cli = _clientes.pop(lugar.nombre, None)
    if cli is not None:
        try:
            cli.close()
        except Exception:
            pass


def _ejecutar_remoto(lugar, argv, *, cwd, entrada, timeout,
                     env_extra=None) -> Resultado:
    # _quote y "cd &&" asumen shell POSIX: un Windows remoto por SSH aún no
    # está soportado; fallar con mensaje claro en vez de mandar sintaxis rota.
    if getattr(lugar, "es_windows", False):
        raise TransporteError(
            f"El lugar '{lugar.nombre}' es Windows remoto: la ejecución remota "
            f"de Witral hoy asume shell POSIX (quoting y 'cd &&'). No soportado."
        )
    if isinstance(argv, list):
        linea = " ".join(_quote(a) for a in argv)
    else:
        linea = argv
    if env_extra:
        prefijo = " ".join(f"{k}={_quote(str(v))}" for k, v in env_extra.items())
        linea = f"{prefijo} {linea}"
    if cwd:
        linea = f"cd {_quote(cwd)} && {linea}"
    import socket as _socket

    # Hasta 2 intentos, pero SOLO reintenta si el canal no llegó a abrirse (el
    # comando no corrió): así absorbe la conexión cacheada muerta (WinError
    # 10054, "reset by peer") sin arriesgar repetir un comando ya lanzado.
    intentos = 2
    for intento in range(1, intentos + 1):
        cli = _cliente_ssh(lugar)
        try:
            stdin, stdout, stderr = cli.exec_command(linea, timeout=timeout)
        except Exception as e:
            if intento < intentos and _es_error_conexion(e):
                _descartar_cliente(lugar)
                continue
            raise TransporteError(_diagnostico_ssh(e, lugar.nombre))
        try:
            # EOF de stdin SIEMPRE: sin esto, cualquier comando remoto que lea
            # stdin (python -c con sys.stdin, cat, psql, etc.) queda esperando
            # entrada hasta el timeout del MCP (el cuelgue de 4 minutos).
            if entrada is not None:
                stdin.write(entrada)
            stdin.channel.shutdown_write()
            salida = stdout.read().decode("utf-8", "replace")
            error = stderr.read().decode("utf-8", "replace")
            codigo = stdout.channel.recv_exit_status()
        except _socket.timeout:
            # Mismo contrato que en local: timeout => código 124, no excepción.
            return Resultado(124, "", f"timeout tras {timeout}s (remoto {lugar.nombre})")
        except Exception as e:
            # Cayó DESPUÉS de lanzar el comando: no se sabe si corrió parcial.
            # No reintentar en silencio (podría duplicar una escritura); avisar.
            _descartar_cliente(lugar)
            if _es_error_conexion(e):
                return Resultado(1, "", (
                    f"conexión perdida durante el comando en {lugar.nombre} ({e}). "
                    f"No se reintentó para no duplicar efectos; verificá el estado "
                    f"con una lectura y reintentá si corresponde."))
            raise TransporteError(_diagnostico_ssh(e, lugar.nombre))
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
