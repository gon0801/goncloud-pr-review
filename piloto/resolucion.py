"""Archivo de prueba del piloto T16 (caso de resolución): errores a propósito.

`ultimo` ya se arregló; `promedio` y `es_par` siguen con su defecto.
"""


def promedio(valores):
    return sum(valores) / len(valores)


def ultimo(lista):
    return lista[-1]


def es_par(numero):
    return numero % 2 == 1
