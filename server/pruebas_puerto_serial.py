"""
Pruebas del framing serie. Corren sin puerto, sin POS y sin red:

    cd witral/server && .venv\\Scripts\\python.exe pruebas_puerto_serial.py

Lo que se verifica y por que:

1. CRC-16/ARC contra el vector estandar ("123456789" -> 0xBB3D). Es la unica
   forma honesta de saber que se implemento ESA variante y no otra de las
   cuatro que se llaman "CRC-16".
2. Ida y vuelta: lo que enmarca esta herramienta lo desenmarca esta herramienta.
3. El caso que ya rompio al parser del POS: un CRC cuyos bytes valen 0x02 (STX)
   o 0x03 (ETX). Si el desenmarcador los interpreta en vez de consumirlos, la
   trama se pierde o el checksum se re-arma sobre basura.
4. Trama con CRC corrupto -> se reporta invalida, no se cuela.
5. Bytes sueltos (ACK/NAK/EOT) fuera de trama: tienen que quedar aparte y no
   contaminar el payload.
"""

from witral.puerto_serial import crc16_arc, enmarcar, _Desenmarcador, STX, ETX, NAK, EOT

fallos = 0


def chequear(nombre, ok, detalle=""):
    global fallos
    if ok:
        print(f"ok: {nombre}")
    else:
        fallos += 1
        print(f"FALLA: {nombre} {detalle}")


# 1. Vector estandar de CRC-16/ARC
chequear("CRC-16/ARC del vector estandar '123456789' = 0xBB3D",
         crc16_arc(b"123456789") == 0xBB3D,
         f"-> 0x{crc16_arc(b'123456789'):04X}")

# 2. Ida y vuelta
payload = b'{"transactionType":"ECHOTEST","mid":"1000829"}'
trama = enmarcar(payload)
chequear("la trama empieza con STX y trae ETX antes del CRC",
         trama[0] == STX and trama[-3] == ETX)
d = _Desenmarcador()
salidas = d.ingerir(trama)
chequear("ida y vuelta: se recupera el payload con CRC valido",
         salidas == [(payload, True)], f"-> {salidas}")

# 3. CRC cuyos bytes valen STX/ETX — el caso que rompio al POS.
#    Se buscan payloads cuyo CRC contenga 0x02 o 0x03 y se comprueba que igual
#    se desenmarcan bien.
encontrados = 0
for i in range(4000):
    p = f'{{"n":{i}}}'.encode()
    t = enmarcar(p)
    lo, hi = t[-2], t[-1]
    if lo in (STX, ETX) or hi in (STX, ETX):
        encontrados += 1
        dd = _Desenmarcador()
        r = dd.ingerir(t)
        chequear(f"CRC con byte 0x{lo:02X}/0x{hi:02X} (payload {p!r}) no rompe el desenmarcado",
                 r == [(p, True)], f"-> {r}")
        if encontrados >= 6:
            break
chequear("se encontraron casos con CRC conteniendo STX/ETX para probar",
         encontrados > 0, f"-> {encontrados}")

# 4. CRC corrupto
t = bytearray(enmarcar(b'{"a":1}'))
t[-1] ^= 0xFF
d = _Desenmarcador()
r = d.ingerir(bytes(t))
chequear("un CRC corrupto se reporta invalido", r and r[0][1] is False, f"-> {r}")

# 5. Bytes sueltos fuera de trama
d = _Desenmarcador()
r = d.ingerir(bytes([NAK]) + enmarcar(b'{"a":1}') + bytes([EOT]))
chequear("el payload sale limpio con ruido alrededor", r == [(b'{"a":1}', True)], f"-> {r}")
chequear("NAK y EOT quedan como bytes sueltos",
         NAK in d.sueltos and EOT in d.sueltos, f"-> {bytes(d.sueltos)!r}")

# 6. Trama partida en trozos (lo normal en serie)
d = _Desenmarcador()
t = enmarcar(b'{"parcial":true}')
salidas = []
for i in range(0, len(t), 3):
    salidas += d.ingerir(t[i:i + 3])
chequear("una trama que llega en trozos de 3 bytes se arma igual",
         salidas == [(b'{"parcial":true}', True)], f"-> {salidas}")

print()
print("TODO OK" if not fallos else f"{fallos} FALLA(S)")
raise SystemExit(1 if fallos else 0)
