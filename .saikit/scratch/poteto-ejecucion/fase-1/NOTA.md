# FASE 1: tipos/invariantes del marcador complete/partial (evidencia)

Rama: poteto/fase-1-cobertura (apilada sobre poteto/fase-0-piso).

## Problema
El marcador de cobertura vivia en dos vocabularios de strings sueltos:
- comentario: "complete" | "partial" | None (`reviewed_completion`).
- snapshot: "complete_claim" | "partial" | "unknown".
Traducciones esparcidas en 5 sitios (`review.py`: marcador, porton
incremental, snapshot inicial; `review_domain._cobertura_persistida`;
`review_context.decidir_modo`). Un valor ambiguo (marcador ausente o
corrupto) se trataba distinto segun el sitio: via rapida a `partial` en
uno, `unknown` en otro. Esa es la clase de hueco por la que se pierde un
hallazgo: una revision previa incompleta que pasa por completa habilita
el camino incremental y sus hallazgos se descartan.

## Cambio (aditivo, una ola, sin shims)
`review_domain.py`: frontera nueva junto a las constantes.
- `parse_coverage(valor)`: acepta los dos vocabularios; lo desconocido
  (None, "", "full", mayusculas...) degrada a UNKNOWN, nunca a completo.
- `is_complete(cobertura)`: unico predicado de revision previa completa.
- `marker_of(cobertura)`: snapshot hacia comentario; UNKNOWN rinde
  "partial", nunca "complete".
Migracion de los 5 llamadores en el mismo cambio:
- `review.py::_marcador_cobertura` usa `marker_of`.
- `review.py` porton incremental usa `is_complete(parse_coverage(...))`.
- `review.py` snapshot inicial usa `parse_coverage` (marcador ausente o
  corrupto ahora es UNKNOWN en vez de PARTIAL: es mas honesto y ningun
  porton distingue PARTIAL de UNKNOWN; la bateria lo confirma).
- `review_domain._cobertura_persistida` usa `parse_coverage` (misma tabla).
- `review_context.decidir_modo` usa `is_complete`.
No se cambio el dataclass Snapshot (ola pequena, mismo invariante).

## Pruebas (el candado)
`tests/test_review_domain.py::FronteraDeCobertura` (4 casos nuevos):
tabla de parse en los dos vocabularios, degradacion fail-closed,
predicado unico y marcador que nunca rinde complete sin claim.
- Foco: domain+context 122 OK; review 337 OK (1 skip previo).
- Piso completo: check-f1-verde.log (627+4 OK).

## Archivos
- `review_domain.py` (+22, -6), `review.py` (+4, -9),
  `review_context.py` (+2, -1), `tests/test_review_domain.py` (+4 casos).
- Esta evidencia en `.saikit/scratch/poteto-ejecucion/fase-1/`.
