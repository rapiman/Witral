"""
Prueba offline del codec de datastore_poblar: arma entradas con los helpers de
movil.py y las vuelve a leer con el parser, sin device de por medio.

Verifica lo que puede romper en silencio: que un long no salga como int, que el
string_set (field 6) y el double (field 7) no queden invertidos, y que el
round-trip devuelva exactamente el valor que se pidio escribir.

    python witral/server/pruebas_datastore_poblar.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from witral.movil import (  # noqa: E402
    _ds_encode_value, _ds_entrada, _ds_entradas, _ds_decode_value,
    _ds_tipo_inferido, _DS_TIPO_POR_FIELD,
)

CASOS = [
    # (clave, tipo, valor_escrito, valor_esperado_al_releer)
    ("pos_id", "string", "BCIP1000829091", "BCIP1000829091"),
    ("uri_transaction", "string", "/api/", "/api/"),
    ("vacio", "string", "", ""),
    ("acentos", "string", "Peñaflor ñ", "Peñaflor ñ"),
    ("port_transaction_host", "int", 443, 443),
    ("app_mode", "int", 0, 0),
    ("negativo", "int", -7, -7),
    ("maximo_contactless_clp", "long", 2000000, 2000000),
    ("long_grande", "long", 9007199254740993, 9007199254740993),
    ("flag", "bool", True, True),
    ("flag_off", "bool", False, False),
    ("tasa", "double", 0.5, 0.5),
    ("conjunto", "string_set", ["a", "b"], ["a", "b"]),
]


def main():
    # 1) El mapa de tipos: field 6 es string_set y field 7 es double.
    assert _DS_TIPO_POR_FIELD[6] == "string_set", _DS_TIPO_POR_FIELD
    assert _DS_TIPO_POR_FIELD[7] == "double", _DS_TIPO_POR_FIELD

    # 2) Inferencia de tipo desde JSON.
    assert _ds_tipo_inferido(True) == "bool"
    assert _ds_tipo_inferido(443) == "int"
    assert _ds_tipo_inferido(2 ** 31) == "long"      # ya no cabe en int32
    assert _ds_tipo_inferido(-(2 ** 31) - 1) == "long"
    assert _ds_tipo_inferido(0.5) == "double"
    assert _ds_tipo_inferido(["a"]) == "string_set"
    assert _ds_tipo_inferido("x") == "string"

    # 3) Round-trip completo de un archivo con todos los tipos.
    data = b""
    for clave, tipo, valor, _ in CASOS:
        data += _ds_entrada(clave, _ds_encode_value(tipo, valor))

    leidas = {k: _ds_decode_value(vm) for k, vm, _ in _ds_entradas(data)}
    assert len(leidas) == len(CASOS), f"{len(leidas)} claves, esperaba {len(CASOS)}"

    for clave, tipo, _, esperado in CASOS:
        tipo_leido, valor_leido = leidas[clave]
        assert tipo_leido == tipo, f"{clave}: tipo {tipo_leido}, esperaba {tipo}"
        assert valor_leido == esperado, f"{clave}: {valor_leido!r} != {esperado!r}"

    # 4) El orden de las entradas se conserva (importa para diffs legibles).
    assert [k for k, _, _ in _ds_entradas(data)] == [c[0] for c in CASOS]

    print(f"OK: {len(CASOS)} claves round-trip, {len(data)} bytes.")


if __name__ == "__main__":
    main()
