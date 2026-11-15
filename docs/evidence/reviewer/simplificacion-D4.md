# D4: publicación desde datos tipados

Base y puente de retorno: `4db77cc5ff984d54d7e0ed450456837204ed72af`.
El SHA completo de D4 está en el commit de esta entrega y en el PR. No se puede
escribir el SHA de un commit dentro del mismo commit sin cambiarlo.

## Comportamiento y diff

- `review.py` y `review_coordinator.py` preparan la revisión, los avisos, las
  explicaciones y los fallos con `review_document.py`. Ambos usan
  `publish_checkpoint`, que relee el sticky antes y después de escribir.
- `review_render.py` conserva el formato de líneas y avisos compartidos. Ya no
  contiene `compose`, `compose_with_findings`, sus dos presupuestos ni el
  escritor de banners sobre el cuerpo legado. El coordinador ya no decide qué
  conservar leyendo títulos o avisos Markdown.
- El comentario mantiene el bloque v3 y los marcadores que lee Glee. La
  primera importación de un comentario viejo conserva su texto como histórico,
  sin interpretar sus encabezados como datos tipados.
- Un libro grande puede omitirse con un aviso visible. La prosa se recorta en
  su slot. Si el estado o el cuerpo fijo no caben, se conserva el sticky previo
  y se intenta avisar sin partir el estado. Sin estado previo válido no se crea
  un comentario de sólo aviso.
- `git diff 4db77cc5ff984d54d7e0ed450456837204ed72af..HEAD --stat`
  reproduce el diff D3→D4 después del commit. Los cambios están en
  `review.py`, `review_coordinator.py`, `review_document.py`, `review_render.py`,
  dos scripts de PR20 y verificación, y sus pruebas.

## Pruebas nuevas o migradas

- `test_sanitized_markers_and_numeric_entities_remain_stable` falló antes de
  conservar `&lt;` entre ciclos. Ahora comprueba que `<!--` queda neutralizado y
  `&#78;` se ve como texto sin doble escapado.
- `test_lost_patch_reply_does_not_confirm_direct_publication` falló con el
  escritor directo anterior. Ahora comprueba que una respuesta PATCH perdida
  no confirma la revisión.
- `test_repeated_capacity_notice_does_not_stack` falló al repetir el aviso de
  desborde. Ahora comprueba que dos intentos producen un solo aviso y un PATCH.
- Las pruebas de publicación directa simulan escrituras y relecturas reales del
  comentario. Las de capacidad comprueban el documento válido completo o la
  conservación íntegra del anterior, no un banner sin estado. Las pruebas de
  comandos cambian los encabezados visibles y leen los mismos datos tipados.

## Comprobaciones

- `bash scripts/check.sh`: OK. Python 3.14.7, `pre-commit run --all-files`,
  `actionlint`, partición determinista y **831 pruebas**.
- `python3 -m unittest discover -s tests -p 'test_review_document.py'`:
  33 pruebas, OK. Incluye hashing, capacidad, Glee, escritura incierta y
  relectura.
- `ruff check review.py review_coordinator.py review_document.py review_render.py
  scripts/armar_sticky_pr20.py scripts/verify_sticky.py
  tests/test_review_document.py`: OK. `git diff --check`: OK.
- En un directorio temporal, `git archive` del SHA D3 ejecutó su
  `review_coordinator.publish_checkpoint` en otro proceso con un comentario
  D4. Devolvió `Unconfirmed`, sin llamadas de escritura. D4 volvió a leer y
  preparar el mismo cuerpo byte por byte. No se consultó GitHub ni un modelo.

## Pendientes

Codex debe revisar el diff integrado, sobre todo la importación histórica y
los caminos de capacidad. No se migraron comentarios reales ni se instaló la
action en consumidores. No se hicieron llamadas pagadas ni despliegues.
