"""Archivo de prueba del piloto T16: errores a propósito para ejercer el revisor."""


def promedio(valores):
    return sum(valores) / len(valores)


def ultimo(lista):
    return lista[len(lista)]


def leer_config(ruta):
    archivo = open(ruta)
    datos = archivo.read()
    return eval(datos)


def mediana(valores):
    ordenados = sorted(valores)
    return ordenados[len(ordenados) // 2]


def dividir_todo(valores, divisor):
    return [v / divisor for v in valores if divisor == 0]
