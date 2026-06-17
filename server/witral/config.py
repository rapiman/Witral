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
    # Rutas con nombre dentro del lugar (repo, web, etc.), libres.
    rutas: dict[str, str] = field(default_factory=dict)

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
    )


def _parse_lugar(nombre: str, d: dict) -> Lugar:
    es_local = bool(d.get("local", nombre == LOCAL))
    return Lugar(
        nombre=nombre,
        es_local=es_local,
        raiz=d.get("raiz"),
        sensible=bool(d.get("sensible", False)),
        ssh=_parse_ssh(d["ssh"]) if "ssh" in d else None,
        db=_parse_db(d["db"]) if "db" in d else None,
        rutas=dict(d.get("rutas", {})),
    )


class Config:
    """Conjunto de lugares cargados, con resolución por nombre."""

    def __init__(self, lugares: dict[str, Lugar]):
        self._lugares = lugares

    @property
    def nombres(self) -> list[str]:
        return list(self._lugares.keys())

    def existe(self, nombre: str) -> bool:
        return nombre in self._lugares

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


def cargar() -> Config:
    """Carga la config desde disco. Siempre garantiza un lugar 'local'."""
    ruta = _ruta_config()
    lugares: dict[str, Lugar] = {}

    if ruta.exists():
        try:
            data = json.loads(ruta.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"Config inválida en {ruta}: {e}") from e
        for nombre, d in data.get("lugares", {}).items():
            lugares[nombre] = _parse_lugar(nombre, d)

    # Garantizar 'local'.
    if LOCAL not in lugares:
        lugares[LOCAL] = Lugar(nombre=LOCAL, es_local=True)
    if lugares[LOCAL].raiz is None:
        lugares[LOCAL].raiz = _raiz_local_por_defecto()

    return Config(lugares)
