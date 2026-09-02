"""Comprobaciones de bitacora.anotar: formato, aplanado, tope y que nunca reviente."""
import tempfile
from pathlib import Path

from witral import bitacora as BIT

fallos = 0


def revisar(que, condicion, detalle=""):
    global fallos
    if condicion:
        print(f"  ok  {que}")
    else:
        fallos += 1
        print(f"FALLA  {que}   {detalle}")


tmp = Path(tempfile.mkdtemp())
log = tmp / ".witral" / "cmd.log"

# 1. Linea basica, con las siete columnas.
BIT.anotar(str(tmp), "local", "run", "git status -sb", True, 0, 42)
revisar("crea el archivo", log.exists())
campos = log.read_text(encoding="utf-8").rstrip("\n").split("\t")
revisar("siete columnas", len(campos) == 7, f"son {len(campos)}: {campos}")
revisar("lugar", campos[1] == "local", campos[1])
revisar("tool", campos[2] == "run", campos[2])
revisar("marca de confirmado", campos[3] == "conf", campos[3])
revisar("codigo", campos[4] == "0", campos[4])
revisar("duracion", campos[5] == "42", campos[5])
revisar("comando", campos[6] == "git status -sb", campos[6])

# 2. Sin confirmar se distingue.
BIT.anotar(str(tmp), "wedwed", "run", "ls", False, 1, 7)
campos2 = log.read_text(encoding="utf-8").rstrip("\n").split("\n")[1].split("\t")
revisar("sin confirmar marca '-'", campos2[3] == "-", campos2[3])
revisar("lugar remoto", campos2[1] == "wedwed", campos2[1])

# 3. Multilinea se aplana y no rompe columnas.
BIT.anotar(str(tmp), "local", "run", "linea1\nlinea2\r\nlinea3\tcon tab", True, 0, 1)
l3 = log.read_text(encoding="utf-8").rstrip("\n").split("\n")[2]
revisar("multilinea en una sola linea", l3.count("\n") == 0)
revisar("siete columnas tambien aplanado", len(l3.split("\t")) == 7,
        f"{len(l3.split(chr(9)))}")
revisar("saltos como simbolo", "⏎" in l3, l3.split("\t")[6])

# 4. Comando gigante se corta con marca.
BIT.anotar(str(tmp), "local", "run", "x" * 5000, True, 0, 1)
l4 = log.read_text(encoding="utf-8").rstrip("\n").split("\n")[3].split("\t")[6]
revisar("comando acotado", len(l4) < 2200, f"largo {len(l4)}")
revisar("avisa que corto", "chars]" in l4, l4[-40:])

# 5. Acumula, no pisa.
revisar("cuatro lineas", len(log.read_text(encoding="utf-8").rstrip("\n").split("\n")) == 4)

# 6. Nunca revienta: raiz None, y raiz imposible.
try:
    BIT.anotar(None, "local", "run", "nada", True, 0, 0)
    BIT.anotar("Z:\\no\\existe\\ni\\va\\a\\existir", "local", "run", "nada", True, 0, 0)
    revisar("no revienta con raiz invalida", True)
except Exception as e:
    revisar("no revienta con raiz invalida", False, repr(e))

print()
print("TODO OK" if fallos == 0 else f"{fallos} caso(s) con problema")
raise SystemExit(1 if fallos else 0)
