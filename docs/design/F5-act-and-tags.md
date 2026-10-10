# F5 — Interacción y simulación: plan

9 de octubre de 2026. Fuente: roadmap §9.7 y las corridas del banco (`bench/results/2026-10-09*.md`): en 5 de 7 briefs lo que queda sin verificar depende de un clic, un tipeo o de tags en un estado dado.

## 1. Qué falta verificar hoy
| Brief | Sin verificar | Qué lo cubre |
|---|---|---|
| 01 dashboard | la barra se mueve al cambiar A, B o C; el gris responde al checkbox | tipeo, clic (A) |
| 02 formulario | el botón se habilita con un nombre válido; el read-back del tag | tipeo (A); tags (B) |
| 04 tabla | el label muestra la fila seleccionada | selección de fila (A) |
| 06 alarmas | filtro por área y perfil del journal con alarmas reales | tags en alarma (B) |
| 07 navegación | popup abre y cierra; nav ida y vuelta; el nombre tipeado llega a la embebida | clic, tipeo (A); popups en los ojos (A2) |

## 2. Partes
- **A. Acciones en el navegador (solo Python).** `inspect --act "<pasos>"`: los pasos corren después de abrir la view y antes de medir, así que lo que se mide, el árbol del gateway, los logs y `--expect` ven el estado de después. Un script que falla al hacer clic sale como L001, igual que en `onStartup`.
- **A2. Popups. Hecho el 9 de octubre de 2026.** Un popup abierto se monta en `#popup-<id>` con rutas `P<id>.0:…` y el gateway lo lista con `mountPath` `P<id>`. Después de las acciones, el inspect baja el view.json de cada popup abierto y lo cruza como la view principal (mismas reglas V/Q/L, logs por instancia `<view>@P<id>`); sus componentes se nombran `popup:<id>/root/...` en reglas, `expect` y `query`, y `expect` suma `popup <id> open|closed`. Probado en vivo con el 07 de la corrida 3: abre, mide "P-101"/"En marcha" adentro y cierra. Los pasos también alcanzan componentes del popup (`click popup:<id>/root/Ok`): se resuelven al ejecutar el paso, con el view.json del popup ya abierto; si el popup no está abierto, A004.
- **B. Tags simulados (módulo Java).** Tags de memoria en el sandbox, bajo una carpeta `PDK/` del proveedor, escritos y leídos por el módulo con firma de sandbox: estados normal, alarma y mala calidad, y read-back. Requiere reinstalar el módulo: se pide OK antes. Diseño acordado el 9 de octubre de 2026 (§5).

## 3. Decisiones de A
| # | Decisión | Elegida |
|---|---|---|
| D12 | Dónde viven las acciones | Un flag de `inspect` (`--act`, `act=` en el MCP), no una herramienta aparte: el estado de después solo sirve si se mide en la misma página. |
| D13 | Gramática | Como `--expect`: pasos separados por `;` o líneas, componentes por ruta de nombres. `click <c>`, `type <c> "texto"`, `row <tabla> N` (1 = primera fila visible), `wait <ms>`. |
| D14 | Tipeo | Clic en el componente (sale del modo lectura del numeric entry), seleccionar todo, escribir y Tab: el blur confirma el valor (text field con `deferUpdates`, numeric entry, text area). |
| D15 | Fallas | Un paso que no se puede hacer (componente inexistente, no renderizado, deshabilitado, sin input) corta la secuencia y sale como **A004** con el motivo; uno mal escrito, **A005**. Un botón deshabilitado es información útil, no un error del harness. |
| D16 | Estabilidad | Después de cada paso, la misma espera de geometría quieta que al abrir; después del último, el árbol del gateway espera a que no queden calidades Uncertain, como hoy. |

## 4. Criterio de salida de A
**Cumplido el 9 de octubre de 2026**, en vivo sobre `PDK/_bench/r2/`: los cuatro puntos dan `act: N/N done` y `expect: N/N passed` (04: "Bomba A" con la fila 2; 01: suma 28 con A=20 y gris al tildar; 02: botón habilitado con "Tanque1", A004 "disabled" antes; L001 al hacer clic en un `onClick` sin indentar). El checkbox se clickea por su `label`: el input está oculto detrás de la caja dibujada.

Con `--act` y `--expect`, en las views del banco:
- 04: `row root/Table 1` → el label muestra el nombre de esa fila;
- 01: `type` en A → cambia la suma; `click` en el checkbox → las lecturas cambian de color;
- 02: `type` de un nombre válido → el botón queda habilitado;
- un clic sobre un botón con script roto da L001 en ese botón.

## 5. Decisiones de B
APIs del gateway (8.3.8, `common.jar` y `gateway-api`), vistas con javap: `GatewayContext.getTagManager().getTagProvider(nombre)` → `TagProvider` con `importTagsAsync(base, json, "json", CollisionPolicy)`, `writeAsync(paths, List<QualifiedValue>, SecurityContext)` (la única escritura que lleva calidad), `readAsync`, `browseAsync` y `removeTagConfigsAsync`, todas `CompletableFuture`; `getAlarmManager().queryStatus(AlarmFilter)` devuelve `AlarmEvent` (estado, prioridad, displayPath, fuente). No hay API para forzar una alarma: se activa al evaluar el valor. Maker: hasta 10.000 tags y 10 sesiones de Perspective por gateway; sin límite documentado de alarmas.

| # | Decisión | Elegida |
|---|---|---|
| D17 | Dónde viven los tags | Tags de memoria en `[<proveedor>]PDK/` del proveedor `-Dpdk.tagProvider` (por defecto `default`). No un `ManagedTagProvider` propio: los briefs leen `[default]PDK/...` y un proveedor managed exige write handlers. Los tags son del gateway, no del proyecto: el destino también ve `PDK/`. |
| D18 | Cómo se crean | JSON de exportación estándar de Ignition (el de `system.tag.configure`) por `importTagsAsync` con `Overwrite`: las alarmas se escriben con sus nombres conocidos (`mode`, `setpointA`, `priority`, `displayPath`). Reimportar devuelve los valores declarados, así que `apply` también resetea. El módulo solo acepta carpetas y tags de memoria; rechaza `eventScripts`, pipelines de notificación, historial y nombres con `/` o `[`. Hasta 500 tags por import. |
| D19 | Quién escribe | Solo con `-Dpdk.sandbox` y solo bajo `PDK/` (403 `OUTSIDE_TAG_FOLDER`); nunca propiedades de config (`X.ValueSource`). Leer y browsear, cualquier ruta: un `Bad_NotFound` dice si el tag existe. |
| D20 | Calidad mala | `writeAsync` con `BasicQualifiedValue(v, calidad)`. Si el proveedor de memoria no la respeta: `enabled: false` en el JSON (`Bad_Disabled`) y, si hace falta otra calidad, tag de expresión con `forceQuality`. Probado en vivo el 9 de octubre de 2026: el tag de memoria guarda la calidad escrita (`Bad_Stale` y vuelta a `Good`); `enabled: false` también da `Bad_Disabled`. |
| D21 | Alarmas | Se declaran en el JSON del tag y se disparan escribiendo un valor que cumpla la condición. `GET /alarms` lee su estado filtrando por fuente bajo `PDK/`. Probado: `AboveValue` con `setpointA` 80 pasa a `ActiveUnacked` al escribir 95 y a `ClearUnacked` al volver a 20. Ojo con Maker: pasado el límite de 10.000 tags, los nuevos nacen `Bad_LicenseExceeded` y no aceptan escrituras (`Bad_Unsupported`); al arrancar, los tags que no entran quedan excedidos hasta el próximo reinicio. |
| D22 | Rutas del módulo | `GET /tags?path=&depth=` (browse con valor y calidad), `POST /tags/read`, `PUT /tags?path=` (import), `POST /tags/write` (con read-back), `DELETE /tags?path=`, `GET /alarms?path=`, `POST /alarms/ack?path=` (bajo `PDK/`; `apply` lo usa para que cada corrida empiece sin eventos sin reconocer). |
| D23 | En los ojos | Sin `--sim`: verbos nuevos en `--act` (`tag <ruta> <valor> [quality <Código>]`, `tag <ruta> quality <Código>`, que conserva el valor) y en `--expect` (`tag <ruta> = v`, `tag <ruta> quality <Código|Good|Bad|Uncertain|Error>`, `alarm <ruta> active|cleared`). Como en D12, el estado sirve si se mide en la misma página; la view lo recibe por suscripción. Rutas sin `[proveedor]` cuelgan de `PDK/`. Fallas: A004 (fuera de `PDK/`, inexistente, rechazada), A005, A001. No se restaura nada al terminar. |
| D24 | Fixture y CLI | `pdk tags list|read|write|apply|delete|alarms` y `pdk_tags` en el MCP; G013 para escrituras fuera de `PDK/`; `bench/tags/sandbox.json` con `Sandbox/Setpoint`, `Sandbox/Message` y los tags de Área Norte con alarma. `pdk-brief-runner` aplica la fixture al empezar. |

## 6. Criterio de salida de B
**Cumplido el 9 de octubre de 2026** con `tests/test_gateway_live.py` (`-m gateway`): `tag` + `click` en un botón con script → `root/Reading text "7"` y `tag .../Message = Listo` pasan; `tag ... quality Bad_Stale` da Q001 en el binding del label; un `tag` fuera de `PDK/` da A004 con G013; la alarma `AboveValue` pasa a `ActiveUnacked` y vuelve. En el banco (corrida 4, `bench/results/2026-10-09-r4.md`): el 02 verifica el read-back (6/6) y el 06 muestra alarmas reales de Área Norte (6/6).

En vivo sobre el sandbox: `apply` de la fixture crea los tags; en el 02, `type` + `click` en "Enviar setpoint" y `expect "tag Sandbox/Setpoint = N"` pasa; una `tag ... bad` sobre el tag de un label da Q en ese label; en el 06, escribir sobre el setpoint de una alarma la muestra en la tabla de estado y `alarm ... active` pasa.
