"""
Pasada de idioma: saca el voseo rioplatense de los textos que Witral muestra
(descripciones de tools, mensajes de confirmación, documentación) y lo deja en
español neutro, con construcciones impersonales en infinitivo — que es el
registro que el propio documento ya usa a ratos.

No traduce ni reescribe: aplica reemplazos literales, en orden de más largo a
más corto para que las frases ganen a las palabras sueltas. Preserva el fin de
línea de cada archivo (se abre con newline="").

Uso:  .venv\\Scripts\\python.exe neutralizar_idioma.py [--aplicar]
Sin --aplicar solo informa qué cambiaría.
"""

import sys
from pathlib import Path

# Frases primero: si "vas a borrar" no se resuelve antes que "vas a", queda mal.
REEMPLAZOS = [
    ("vas a borrar", "se va a borrar"),
    ("vas a copiar", "se va a copiar"),
    ("vas a cambiar", "se va a cambiar"),
    ("vas a aplicar", "se va a aplicar"),
    ("no te equivocás", "no se equivoca"),
    ("si lo pasás", "si se pasa"),
    ("que ESPERÁS", "que SE ESPERA"),
    ("lo pasás", "se pasa"),
    ("al toque", "de inmediato"),
    # Coloquialismos que sobreviven sin tilde (la documentación se escribe sin
    # acentos, así que el filtro de arriba no los ve).
    ("que igual pilla el error", "que igual detecta el error"),
    ("esperar el \"dale\"", "esperar la confirmacion explicita"),
    ("con un dale", "con una confirmacion explicita"),
    ("OJO:", "ATENCION:"),
    ("OJO ", "ATENCION "),
    ("Ojo:", "Atencion:"),
    ("Ojo ", "Atencion "),
    # Las formas en -ás/-és van ANTES que el imperativo, o "pasás" quedaría
    # convertido en "pasars".
    ("pasás", "se pasa"),
    ("usás", "se usa"),
    ("dejás", "se deja"),
    ("mirás", "se mira"),
    ("corrés", "se corre"),
    ("verificás", "se verifica"),
    ("Confirmá con el usuario", "Confirmar con el usuario"),
    ("Mostrá al usuario", "Mostrar al usuario"),
    ("Mostrá esto al usuario", "Mostrar esto al usuario"),
    ("Mostrá el comando al usuario", "Mostrar el comando al usuario"),
    ("Reproducí", "Reproducir"),
    ("reintentá", "reintentar"),
    ("Reintentá", "Reintentar"),
    ("confirmá", "confirmar"),
    ("Confirmá", "Confirmar"),
    ("mostrá", "mostrar"),
    ("Mostrá", "Mostrar"),
    ("seguilo", "seguirlo"),
    ("Seguilo", "Seguirlo"),
    ("agregá", "agregar"),
    ("Agregá", "Agregar"),
    ("preferí", "preferir"),
    ("Preferí", "Preferir"),
    ("verificá", "verificar"),
    ("Verificá", "Verificar"),
    ("volvé", "volver"),
    ("Volvé", "Volver"),
    ("dejá", "dejar"),
    ("Dejá", "Dejar"),
    ("elegí", "elegir"),
    ("Elegí", "Elegir"),
    ("escribí", "escribir"),
    ("Escribí", "Escribir"),
    ("probá", "probar"),
    ("Probá", "Probar"),
    ("mirá", "mirar"),
    ("Mirá", "Mirar"),
    ("borrá", "borrar"),
    ("Borrá", "Borrar"),
    ("corré", "correr"),
    ("Corré", "Correr"),
    ("poné", "poner"),
    ("Poné", "Poner"),
    ("sacá", "sacar"),
    ("Sacá", "Sacar"),
    ("andá", "ir"),
    ("fijate", "fijarse"),
    ("Fijate", "Fijarse"),
    ("avisá", "avisar"),
    ("Avisá", "Avisar"),
    ("usá", "usar"),
    ("Usá", "Usar"),
    ("pasá", "pasar"),
    ("Pasá", "Pasar"),
    ("podés", "se puede"),
    ("tenés", "hay que"),
    ("querés", "se quiere"),
    ("acá", "aquí"),
    ("Acá", "Aquí"),
]

OBJETIVOS = [
    Path("witral"),                       # el paquete del servidor
    Path("..") / "WITRAL_PARA_CLAUDE.md",
    Path("..") / "SKILL.md",
    Path("..") / "README.md",
    Path("..") / "INSTALL.md",
]


def archivos():
    for obj in OBJETIVOS:
        if obj.is_dir():
            for p in sorted(obj.rglob("*.py")):
                if "__pycache__" in p.parts or ".venv" in p.parts:
                    continue
                yield p
        elif obj.is_file():
            yield obj


def main():
    aplicar = "--aplicar" in sys.argv
    total = 0
    for ruta in archivos():
        # newline="" también al LEER: read_text traduce CRLF a \n y al escribir
        # se perdería el fin de línea original de la mitad del repo.
        with open(ruta, "r", encoding="utf-8", newline="") as f:
            texto = f.read()
        nuevo = texto
        cuenta = {}
        for viejo, reemplazo in REEMPLAZOS:
            n = nuevo.count(viejo)
            if n:
                cuenta[viejo] = n
                nuevo = nuevo.replace(viejo, reemplazo)
        if not cuenta:
            continue
        n = sum(cuenta.values())
        total += n
        detalle = ", ".join(f"{k}×{v}" for k, v in sorted(cuenta.items()))
        print(f"{ruta}: {n} ({detalle})")
        if aplicar:
            with open(ruta, "w", encoding="utf-8", newline="") as f:
                f.write(nuevo)
    print(f"\nTOTAL: {total} reemplazos" + ("" if aplicar else " (simulación; "
                                            "correr con --aplicar)"))


if __name__ == "__main__":
    main()
