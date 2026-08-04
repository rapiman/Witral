"""
Comunicación por puerto serie, genérica.

//SEN20260804 Nace para poder manejar una caja/ECR contra un POS, pero a
propósito NO sabe nada de ese protocolo en particular: el framing es un
PARÁMETRO. Con `framing="crudo"` esto le habla a cualquier cosa que escuche por
serial —un módem, una balanza, una impresora, una placa— y con
`framing="stx_etx_crc16arc"` habla el protocolo del POS. Si mañana aparece otro
equipo con otro encuadre, se agrega un framing más y el resto no se toca.

POR QUÉ EL CRC LO CALCULA LA HERRAMIENTA Y NO EL LLAMADOR
Porque un CRC calculado del lado del que llama es caro de escribir en cada
llamada y, sobre todo, es un lugar donde uno se equivoca EN SILENCIO: un CRC
mal calculado no se ve, solo produce un NAK del otro lado y un rato de
desconcierto. Acá el llamador manda el texto y nada más.

SOBRE EL NOMBRE DEL FRAMING
Se llama `stx_etx_crc16arc` y no `stx_etx_crc16` porque "CRC-16" a secas es
ambiguo: ARC, MODBUS, CCITT y XMODEM son los cuatro "CRC-16" y dan resultados
distintos. Este es ARC (polinomio 0xA001 reflejado, init 0x0000).
"""

from __future__ import annotations

import time

from .config import Lugar

STX = 0x02
ETX = 0x03
EOT = 0x04
ACK = 0x06
NAK = 0x15

FRAMINGS = ("crudo", "stx_etx_crc16arc")

# Tope duro por llamada: el cliente MCP corta las llamadas largas. Una operación
# que espera a una persona (tarjeta, PIN) NO se sigue desde acá: se dispara y se
# asierta por otro lado.
TOPE_TIMEOUT_S = 40


def crc16_arc(data: bytes) -> int:
    """CRC-16/ARC: poly 0xA001 (reflejado), init 0x0000, sin xor final."""
    crc = 0x0000
    for b in data:
        crc ^= b & 0xFF
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
            crc &= 0xFFFF
    return crc & 0xFFFF


def enmarcar(payload: bytes) -> bytes:
    """STX + payload + ETX + CRC(lo) + CRC(hi). El CRC cubre payload+ETX."""
    core = payload + bytes([ETX])
    crc = crc16_arc(core)
    return bytes([STX]) + core + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


class _Desenmarcador:
    """
    Extrae tramas STX...ETX+CRC de un flujo de bytes.

    //SEN20260804 Los DOS bytes del CRC se consumen SIN interpretarlos. Es el
    error que ya cometió (y corrigió) el parser del POS: si un byte del CRC vale
    0x02 se reinicia la trama y se pierde entera, y si vale 0x03 se re-arma el
    checksum sobre basura. El CRC es binario y puede tener cualquier valor.
    """

    def __init__(self) -> None:
        self.buf = bytearray()
        self.en_trama = False
        self.cuerpo = bytearray()
        self.faltan_crc = 0
        self.crc_rx = bytearray()
        self.sueltos = bytearray()  # ACK/NAK/EOT y ruido fuera de trama

    def ingerir(self, datos: bytes) -> list[tuple[bytes, bool]]:
        """Devuelve [(payload, crc_ok), ...] con las tramas completadas."""
        salidas: list[tuple[bytes, bool]] = []
        for b in datos:
            if self.faltan_crc:
                self.crc_rx.append(b)
                self.faltan_crc -= 1
                if self.faltan_crc == 0:
                    esperado = crc16_arc(bytes(self.cuerpo) + bytes([ETX]))
                    recibido = self.crc_rx[0] | (self.crc_rx[1] << 8)
                    salidas.append((bytes(self.cuerpo), esperado == recibido))
                    self.en_trama = False
                    self.cuerpo = bytearray()
                    self.crc_rx = bytearray()
                continue

            if not self.en_trama:
                if b == STX:
                    self.en_trama = True
                    self.cuerpo = bytearray()
                else:
                    self.sueltos.append(b)
                continue

            if b == ETX:
                self.faltan_crc = 2
                self.crc_rx = bytearray()
            else:
                self.cuerpo.append(b)
        return salidas


def _abrir(pyserial, puerto: str, baud: int, bits: int, paridad: str, stop: float):
    par = {"N": pyserial.PARITY_NONE, "E": pyserial.PARITY_EVEN,
           "O": pyserial.PARITY_ODD, "M": pyserial.PARITY_MARK,
           "S": pyserial.PARITY_SPACE}[paridad.upper()[0]]
    stopbits = {1: pyserial.STOPBITS_ONE, 1.5: pyserial.STOPBITS_ONE_POINT_FIVE,
                2: pyserial.STOPBITS_TWO}[stop]
    return pyserial.Serial(port=puerto, baudrate=baud, bytesize=bits,
                           parity=par, stopbits=stopbits, timeout=0.05,
                           write_timeout=5)


def serial_puertos(lugar: Lugar) -> str:
    if lugar.nombre != "local":
        return ("error: por ahora los puertos serie solo se manejan en 'local' "
                "(el puerto es físico de la máquina que corre Witral).")
    try:
        import serial.tools.list_ports as lp
    except ImportError:
        return "error: falta pyserial en el entorno de Witral (uv add pyserial)."
    puertos = list(lp.comports())
    if not puertos:
        return "(no hay puertos serie visibles)"
    filas = [f"{p.device:<8} {p.description}" for p in puertos]
    return f"{len(puertos)} puerto(s) serie:\n" + "\n".join(filas)


def serial_enviar(lugar: Lugar, puerto: str, texto: str, baud: int = 9600,
                  bits: int = 8, paridad: str = "N", stop: float = 1,
                  framing: str = "crudo", ack: bool = True, timeout: int = 30,
                  reintentos: int = 3, hexa: bool = False) -> str:
    """Envía por el puerto y espera la respuesta. Ver el docstring del módulo."""
    if lugar.nombre != "local":
        return ("error: por ahora los puertos serie solo se manejan en 'local' "
                "(el puerto es físico de la máquina que corre Witral).")
    if framing not in FRAMINGS:
        return f"error: framing '{framing}' desconocido. Disponibles: {', '.join(FRAMINGS)}."
    try:
        import serial as pyserial
    except ImportError:
        return "error: falta pyserial en el entorno de Witral (uv add pyserial)."

    timeout = min(max(1, timeout), TOPE_TIMEOUT_S)
    carga = texto.encode("utf-8")

    try:
        sp = _abrir(pyserial, puerto, baud, bits, paridad, stop)
    except Exception as e:
        return f"error abriendo {puerto}: {e}"

    def hexdump(b: bytes) -> str:
        return " ".join(f"{x:02X}" for x in b)

    try:
        sp.reset_input_buffer()

        if framing == "crudo":
            sp.write(carga)
            sp.flush()
            t0 = time.time()
            eco = bytearray()
            while time.time() - t0 < timeout:
                trozo = sp.read(4096)
                if trozo:
                    eco.extend(trozo)
                elif eco:
                    break  # llegó algo y paró de llegar: se da por completo
            if not eco:
                return f"sin respuesta de {puerto} tras {timeout}s (enviados {len(carga)} bytes)."
            extra = f"\nhex: {hexdump(bytes(eco))}" if hexa else ""
            return (f"OK crudo: enviados {len(carga)} B, recibidos {len(eco)} B.\n"
                    f"{eco.decode('utf-8', errors='replace')}{extra}")

        # --- stx_etx_crc16arc
        trama = enmarcar(carga)
        sp.write(trama)
        sp.flush()
        enviados = 1
        des = _Desenmarcador()
        t0 = time.time()
        aviso = ""

        while time.time() - t0 < timeout:
            trozo = sp.read(4096)
            if trozo:
                for payload, crc_ok in des.ingerir(trozo):
                    if crc_ok:
                        # El POS espera ACK de la caja: sin esto reenvía la
                        # respuesta y parece que llegara duplicada.
                        if ack:
                            sp.write(bytes([ACK]))
                            sp.flush()
                        extra = f"\nhex trama enviada: {hexdump(trama)}" if hexa else ""
                        return (f"OK: respuesta recibida ({len(payload)} B), CRC válido"
                                f"{', ACK enviado' if ack else ''}.{aviso}\n"
                                f"{payload.decode('utf-8', errors='replace')}{extra}")
                    if ack:
                        sp.write(bytes([NAK]))
                        sp.flush()
                    aviso += "\n(se recibió una trama con CRC inválido; se mandó NAK)"

                # Bytes fuera de trama: es donde viajan NAK y EOT.
                if NAK in des.sueltos:
                    des.sueltos = bytearray(x for x in des.sueltos if x != NAK)
                    if enviados <= reintentos:
                        sp.write(trama)
                        sp.flush()
                        enviados += 1
                        aviso += f"\n(el otro lado mandó NAK; reenvío {enviados}/{reintentos + 1})"
                    else:
                        return (f"falló: el otro lado siguió mandando NAK tras {enviados} "
                                f"envíos. CRC o parámetros del puerto mal.{aviso}")
                if EOT in des.sueltos:
                    return (f"falló: el otro lado abandonó (EOT) tras {enviados} envío(s). "
                            f"Suele ser CRC inválido repetido.{aviso}")
            time.sleep(0.01)

        pendiente = ""
        if des.sueltos:
            pendiente = f" Bytes sueltos: {hexdump(bytes(des.sueltos))}."
        elif des.en_trama:
            pendiente = f" Había una trama a medio llegar ({len(des.cuerpo)} B)."
        return (f"sin respuesta completa de {puerto} tras {timeout}s "
                f"({enviados} envío(s)).{pendiente}{aviso}\n"
                f"hex trama enviada: {hexdump(trama)}")
    except Exception as e:
        return f"error en {puerto}: {e}"
    finally:
        try:
            sp.close()
        except Exception:
            pass
