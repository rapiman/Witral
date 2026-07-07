"""
Carga y resolución de la configuración de lugares de Witral.

Un "lugar" es una máquina (local o remota) con todo lo necesario para operar en
ella: acceso SSH, rutas relevantes y cómo invocar psql contra su base local.

La config vive en un archivo JSON cuya ruta se toma de la variable de entorno
WITRAL_CONFIG, o por defecto en el mismo directorio del paquete: lugares.json.

El lugar "local" es implícito: representa esta máquina. Puede igualmente
declararse en el archivo para fijarle una raíz autorizada distinta; si no, se
usa WITRAL_RAIZ o un valor por defecto.

NINGÚN secreto se expone fuera de este módulo hacia el modelo: las tools
trabajan por nombre de lugar y este módulo resuelve internamente las
credenciales necesarias para abrir conexiones.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


LOCAL = "local"


class ConfigError(Exception):
    """Problema al cargar o resolver la configuración."""


@dataclass
class SSHConfig:
    host: str
    usuario: str
    puerto: int = 22
    # Autenticación por clave. Nunca por contraseña en texto plano por el chat;
    # si se define password aquí es responsabilidad del archivo de config local.
    clave: str | None = None          # ruta a la clave privada
    password: str | None = None       # opcional, solo desde el archivo de config
    passphrase: str | None = None     # passphrase de la clave, si la tiene


@dataclass
class DBConfig:
    motor: str = "postgres"
    host: str = "127.0.0.1"           # local para el lugar
    puerto: int = 5432
    base: str | None = None
    usuario: str | None = None
    password: str | None = None
    # Cómo invocar el cliente en ese lugar; por defecto "psql".
    cliente: str = "psql"
    # Autenticación "peer" (socket local + usuario del sistema): si se define,
    # el comando se ejecuta como `sudo -u <como> psql ...` y se omiten -h/-U
    # (psql conecta por socket Unix como ese usuario del SO). Para bases que no
    # usan password TCP, p. ej. un postgres de dev con peer auth. Solo remoto/unix.
    como: str | None = None


@dataclass
class Identidad:
    """Identidad git: autor de los commits. 'usuario_git' se reserva para
    uso futuro (enrutar el remoto a una cuenta); hoy solo se usa autor."""
    nombre: str
    email: str
    usuario_git: str | None = None


@dataclass
class Lugar:
    nombre: str
    es_local: bool = False
    # Raíz autorizada para operaciones de archivo en este lugar.
    raiz: str | None = None
    # Entorno sensible (p. ej. prod) => confirmaciones reforzadas.
    sensible: bool = False
    ssh: SSHConfig | None = None
    db: DBConfig | None = None
    # Sistema operativo del lugar: "windows" o "unix" (linux/mac).
    # Decide la sintaxis de las tools de sistema (procesos, servicios, etc.).
    so: str = "unix"
    # Nombre de la identidad git por defecto para repos en este lugar.
    identidad: str | None = None
    # Rutas con nombre dentro del lugar (repo, web, etc.), libres.
    rutas: dict[str, str] = field(default_factory=dict)

    @property
    def es_windows(self) -> bool:
        return self.so == "windows"

    def requiere_ssh(self) -> SSHConfig:
        if self.es_local:
            raise ConfigError(f"El lugar '{self.nombre}' es local; no usa SSH.")
        if self.ssh is None:
            raise ConfigError(f"El lugar '{self.nombre}' no tiene config SSH.")
        return self.ssh

    def requiere_db(self) -> DBConfig:
        if self.db is None:
            raise ConfigError(f"El lugar '{self.nombre}' no tiene config de base.")
        return self.db


def _ruta_config() -> Path:
    env = os.environ.get("WITRAL_CONFIG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "lugares.json"


def _raiz_local_por_defecto() -> str:
    return os.environ.get(
        "WITRAL_RAIZ",
        str(Path.home() / "Documents" / "Proyectos"),
    )


def _parse_ssh(d: dict) -> SSHConfig:
    return SSHConfig(
        host=d["host"],
        usuario=d["usuario"],
        puerto=int(d.get("puerto", 22)),
        clave=d.get("clave"),
        password=d.get("password"),
        passphrase=d.get("passphrase"),
    )


def _parse_db(d: dict) -> DBConfig:
    return DBConfig(
        motor=d.get("motor", "postgres"),
        host=d.get("host", "127.0.0.1"),
        puerto=int(d.get("puerto", 5432)),
        base=d.get("base"),
        usuario=d.get("usuario"),
        password=d.get("password"),
        cliente=d.get("cliente", "psql"),
        como=d.get("como"),
    )


def _parse_identidad(d: dict) -> Identidad:
    return Identidad(
        nombre=d["nombre"],
        email=d["email"],
        usuario_git=d.get("usuario_git"),
    )


def _so_por_defecto(es_local: bool) -> str:
    """SO por defecto: autodetecta el local; remotos asumen unix."""
    if es_local:
        import platform
        return "windows" if platform.system() == "Windows" else "unix"
    return "unix"


def _parse_lugar(nombre: str, d: dict) -> Lugar:
    es_local = bool(d.get("local", nombre == LOCAL))
    so = d.get("so")
    if so:
        so = "windows" if so.lower().startswith("win") else "unix"
    else:
        so = _so_por_defecto(es_local)
    return Lugar(
        nombre=nombre,
        es_local=es_local,
        raiz=d.get("raiz"),
        sensible=bool(d.get("sensible", False)),
        ssh=_parse_ssh(d["ssh"]) if "ssh" in d else None,
        db=_parse_db(d["db"]) if "db" in d else None,
        so=so,
        identidad=d.get("identidad"),
        rutas=dict(d.get("rutas", {})),
    )


class Config:
    """Conjunto de lugares cargados, con resolución por nombre."""

    def __init__(self, lugares: dict[str, Lugar],
                 identidades: dict[str, Identidad] | None = None,
                 error_config: str | None = None):
        self._lugares = lugares
        self._identidades = identidades or {}
        # Si la config no pudo cargarse (JSON roto, etc.), aquí queda el
        # detalle. El servidor arranca igual, pero las tools lo reportan.
        self.error_config = error_config

    @property
    def nombres(self) -> list[str]:
        return list(self._lugares.keys())

    @property
    def identidades(self) -> list[str]:
        return list(self._identidades.keys())

    def existe(self, nombre: str) -> bool:
        return nombre in self._lugares

    def identidad(self, nombre: str) -> Identidad:
        """Resuelve una identidad por nombre. Error si no existe."""
        if nombre not in self._identidades:
            disponibles = ", ".join(self._identidades.keys()) or "(ninguna)"
            raise ConfigError(
                f"Identidad desconocida: '{nombre}'. "
                f"Identidades definidas: {disponibles}."
            )
        return self._identidades[nombre]

    def resolver(self, nombre: str | None) -> Lugar:
        """
        Devuelve el Lugar para 'nombre'. None o 'local' => el lugar local.
        Un nombre desconocido es un destino NUEVO: se lanza error para que la
        capa de tools pida confirmación al usuario en vez de conectar a ciegas.
        """
        if nombre is None or nombre == LOCAL:
            return self._lugares[LOCAL]
        if nombre not in self._lugares:
            raise DestinoDesconocido(nombre, self.nombres)
        return self._lugares[nombre]


class DestinoDesconocido(ConfigError):
    """
    Se pidió operar en un lugar que no está en config. La capa superior debe
    tratar esto como 'destino nuevo' y pedir confirmación explícita al usuario,
    nunca conectar automáticamente.
    """

    def __init__(self, nombre: str, conocidos: list[str]):
        self.nombre = nombre
        self.conocidos = conocidos
        super().__init__(
            f"Lugar desconocido: '{nombre}'. "
            f"Lugares definidos: {', '.join(conocidos) or '(ninguno)'}. "
            f"Es un destino nuevo; requiere confirmación del usuario."
        )


def _formatear_error_json(ruta, texto: str, e: "json.JSONDecodeError") -> str:
    """Arma un mensaje claro de error JSON con la línea señalada y un puntero."""
    lineas = texto.splitlines()
    n = e.lineno
    bloque = []
    # Mostrar la línea problemática y una de contexto antes/después.
    for i in range(max(1, n - 1), min(len(lineas), n + 1) + 1):
        marca = ">>" if i == n else "  "
        bloque.append(f"  {marca} {i:>3} | {lineas[i - 1]}")
        if i == n:
            bloque.append("        " + " " * (e.colno + 2) + "^")
    contexto = "\n".join(bloque)
    return (
        f"Config inválida en {ruta}\n"
        f"  {e.msg} (línea {e.lineno}, columna {e.colno})\n\n"
        f"{contexto}\n\n"
        f"  Pistas frecuentes: coma de más antes de }} o ], falta una coma "
        f"entre bloques, comillas simples en vez de dobles, o una llave suelta."
    )


def cargar() -> Config:
    """Carga la config desde disco. Siempre garantiza un lugar 'local'.

    NUNCA lanza por config inválida. Si el JSON está roto, arranca solo con
    'local'; si el JSON parsea pero un lugar/identidad está mal formado, se
    cargan igual los válidos (fail-soft por lugar). El detalle del error
    queda en Config.error_config para que las tools lo reporten.
    """
    ruta = _ruta_config()
    lugares: dict[str, Lugar] = {}
    identidades: dict[str, Identidad] = {}
    error_config: str | None = None

    if ruta.exists():
        texto = ruta.read_text(encoding="utf-8-sig")
        data = None
        try:
            data = json.loads(texto)
        except json.JSONDecodeError as e:
            # JSON roto: no se puede rescatar nada; solo queda 'local'.
            error_config = _formatear_error_json(ruta, texto, e)
        if data is not None:
            # Fail-soft POR LUGAR: un lugar/identidad mal formado no tumba a
            # los demás; se cargan los válidos y se reporta solo lo roto.
            rotos: list[str] = []
            for nombre, d in (data.get("lugares", {}) or {}).items():
                try:
                    lugares[nombre] = _parse_lugar(nombre, d)
                except (KeyError, TypeError, ValueError) as e:
                    rotos.append(f"lugar '{nombre}': {e}")
            for nombre, d in (data.get("identidades", {}) or {}).items():
                try:
                    identidades[nombre] = _parse_identidad(d)
                except (KeyError, TypeError, ValueError) as e:
                    rotos.append(f"identidad '{nombre}': {e}")
            if rotos:
                error_config = (
                    f"Config parcialmente inválida en {ruta}. Se cargaron los "
                    f"lugares/identidades válidos; con errores:\n  - "
                    + "\n  - ".join(rotos)
                )

    # Garantizar 'local' (siempre, incluso si la config falló).
    if LOCAL not in lugares:
        lugares[LOCAL] = Lugar(nombre=LOCAL, es_local=True,
                               so=_so_por_defecto(True))
    if lugares[LOCAL].raiz is None:
        lugares[LOCAL].raiz = _raiz_local_por_defecto()

    return Config(lugares, identidades, error_config)
