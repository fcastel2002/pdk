# F3 — Ojos: plan por sesiones

Borrador del 5 de octubre de 2026, para aprobar antes de escribir código. Fuente: roadmap §9.5, javap sobre `perspective-gateway-3.3.9.jar` y el runtime de perspective-compiler (`runtime/harness.py`, `rules.py`, `mapping.py`, `models.py`).

## 1. Hallazgos

### SDK de Perspective 3.3.9 (javap)
- `PerspectiveContext.get(gatewayContext).getSessionMonitor()` → `PerspectiveSessionMonitor` (clase concreta, no interfaz): `getClientSessionsForProject(p)`, `findSession(UUID)`, `findDesignerSession(p)`. Devuelve `InternalSession`, que es **interna**: va detrás del adapter.
- `Session` (pública): `queue()` (`ExecutionQueue`, para leer en el hilo de la sesión), `getPages()`, `findPage(id)`, `refreshBinding`.
- `Page`: `getId()`, `getCreated()`, `getViews()` → `ViewModel`. `PageModel` ya trae `report()`/`briefReport()` en JSON: a evaluar en el spike antes de serializar a mano.
- `ViewModel`: `getId()` (`ViewInstanceId` con `resourcePath` y `mountPath`), `getRootContainer()`, `findChildByIndexLocation`, `getPropertyTreeOf(PropertyType)` (params/custom de la view), `getViewState()`, `getViewInfo()`.
- `Component` (pública): `getComponentAddressPath()`, `getType()`, `getChildren()`, `getPropertyConfig(PropertyKey)`, `fireEvent` (F5). `ComponentModel.getPropertyTreeOf(props|position|custom|meta)` y `getComponentState()`.
- `PropertyTree`: `read(path)` → `QualifiedValue` (valor + calidad), `toJson()`, `getRootKeys()`, `subscribe`.
- `PropertyConfig.binding` (`BindingConfig`) y `hasEnabledBinding()`: el gateway dice qué props tienen binding configurado; el valor y la calidad salen del `PropertyTree`.
- Componentes y vistas llaman a `mdcSetup()`: los logs de scripts deberían llevar en el MDC la sesión, la view y el componente. Logback 1.3 está en el classpath del gateway, así que el módulo puede colgar un appender propio y capturar WARN/ERROR con ese contexto (a confirmar en el spike).

### perspective-compiler: qué se reutiliza
- **DOM:** cada componente lleva `data-component` (tipo) y `data-component-path`. La raíz es `C.0` y los hijos se direccionan por índice (`C.0:1:2`). Los descendientes de views embebidas llevan `$`; las docks, `T[`/`L[`/`R[`/`B[`. Con eso el join con el `view.json` es directo: `C.0:i:j` ↔ `/root/children/i/children/j`, **sin IR**.
- `open_ready_page`: contexto aislado, navegación, espera de la raíz visible, «View Not Found», sesiones excedidas, banner de conexión, cierre del aviso de Maker, DPR 1 y fuentes cargadas. Se porta casi entero.
- Estabilidad: animaciones terminadas y geometría quieta en 3 muestras de `requestAnimationFrame` (tolerancia medida: 0 px).
- Extracción (`_EXTRACT_JS`): cajas, overflow, texto con sus rects y clip por ancestros. Se porta tal cual.
- Reglas VIS001 (texto recortado), VIS002 (desborde de componente), VIS003 (desborde del viewport) y VIS007 (componente colapsado), con supresión de cascadas. Se portan sobre la nueva forma del snapshot.
- **Se descarta:** hashes de deploy, IR, `identity_map` de tres vías, `policy` compiler/audit, manifest y artefactos por viewport. En PDK la firma del deploy ya identifica la versión.

### Entorno
- Playwright ya tiene Chromium 1243 en `%LOCALAPPDATA%\ms-playwright` (lo usó perspective-compiler con `playwright>=1.58`).
- La ruta de preview `/pdk/<view>` (F2) es la URL que abren los ojos. En Maker aparece el aviso «Personal Use Only», que el harness ya sabe cerrar.
- perspective-compiler tuvo que limitar las sesiones concurrentes en Maker (`session_gate`): hay que reutilizar sesión en lugar de abrir una por inspección.

## 2. Decisiones (a confirmar)
| # | Decisión | Recomendación | Alternativa |
|---|---|---|---|
| D6 | Cómo encontrar la sesión/página que abrió Chromium | Spike: leer el id de página desde el cliente (JS en la página) y pedírselo al gateway. Si el cliente no lo expone, la página más nueva del proyecto sandbox cuya view tenga ese `resourcePath`. | Siempre la heurística de «la más nueva». |
| D7 | Forma del árbol | El módulo devuelve por componente: address path, tipo, nombre, `props/position/custom/meta` en JSON y **solo** las calidades no-Good, más la lista de props con binding (`scope.path → tipo`). El filtrado y el presupuesto, en Python. | Volcar `PageModel.report()` crudo si ya trae todo; se decide en el spike. |
| D8 | Logs de scripts | Appender de logback en el módulo: buffer en memoria de WARN/ERROR con MDC (sesión, view, componente), servido por `/logs?since=N`. Si el MDC no trae el componente, se atribuye a la view. | Leer `/data/api/v1/logs` de 8.3 si existe y alcanza; sin componente. |
| D9 | Ciclo de vida del navegador | En el MCP, un Chromium headless persistente (una sesión Perspective por view abierta, reutilizada entre `pdk_open`/`pdk_inspect`/`pdk_screenshot`). En la CLI, `pdk inspect` hace todo de una vez. | Abrir y cerrar en cada llamada (más lento y gasta sesiones en Maker). |
| D10 | Dependencia | `playwright` como dependencia normal, con import perezoso (validar no lo necesita). | Grupo opcional `eyes`. |
| D11 | Viewport | Uno por defecto, 1366×768 (el chico de compiler), configurable con `--viewport 1920x1080`. | Los dos siempre (dobla tiempo y tokens). |

## 3. Diseño

### Módulo (Java, fino; adapter `Runtime` con lo interno)
| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/sessions?project=` | Sesiones cliente del proyecto: id, uptime y páginas (id, creada, views con `resourcePath`, `mountPath` e id de instancia) |
| GET | `/sessions/{sid}/pages/{pid}/views/{vid}/tree` | Árbol de la view leído dentro de `session.queue()` con timeout (forma de D7) |
| GET | `/logs?since=N&session=` | Entradas WARN/ERROR capturadas, con MDC (D8) |

- Solo lectura: alcanza con el token, no necesita la propiedad de escritura.
- Todo lo de `InternalSession` y `PerspectiveSessionMonitor` queda en una sola clase. Si no enlaza (`LinkageError`) o falla, las rutas devuelven `501 RUNTIME_UNAVAILABLE` y Python degrada a ojos solo de DOM con un warning.
- `/health` suma `runtime: true|false`.

### Python: `pdk/eyes/`
- `browser.py`: port de `open_ready_page`, estabilidad, extracción, consola (`console`/`pageerror`) y captura PNG. Import perezoso de Playwright.
- `runtime.py`: cliente de `/sessions`, `/tree` y `/logs`, e identificación de la página (D6).
- `join.py`: address path ↔ `data-component-path` ↔ pointer del `view.json` local (el de `work/`, con su firma). Una diferencia de tipo en el mismo path o una firma distinta a la desplegada se informan como desincronización y cortan el reporte.
- `rules.py`: diagnósticos con la misma forma que los del validador (`code`, `severity`, `pointer`, `component`, `message`, `hint`), así se reutiliza el render con presupuesto y `focus`. Códigos nuevos, estables:

| Código | Qué | Fuente |
|---|---|---|
| V001 | texto recortado | DOM (VIS001) |
| V002 | desborde de componente | DOM (VIS002) |
| V003 | desborde del viewport | DOM (VIS003) |
| V007 | componente colapsado | DOM (VIS007) |
| V010 | componente del `view.json` que no se montó | join |
| Q001 | prop con binding y calidad Bad (p. ej. `Bad_NotFound`) | árbol |
| Q002 | binding o transform con error (`Error_*`, p. ej. script) | árbol |
| Q003 | calidad Uncertain | árbol (warning) |
| L001 | error de script en el log del gateway | `/logs` |
| C001 | error de consola o excepción en el navegador | Playwright |
| X001 | ojos degradados (sin runtime, sin logs) | — (warning) |

- El reporte sale ordenado por severidad y componente, con conteos, ≤1.500 tokens; `focus=<código|componente|pointer>` da el detalle (valores, calidades, cajas).

### CLI y MCP
- CLI: `pdk open|inspect|screenshot <path> [--viewport]`, y `pdk gateway sessions|logs` para depurar.
- MCP: `pdk_open` (abre la preview y devuelve sesión, página y estado de carga), `pdk_inspect` (reporte de anomalías) y `pdk_screenshot` (imagen PNG como contenido de imagen, por viewport o de un componente). `pdk_logs` no va como herramienta aparte: L001 entra en `pdk_inspect`.

## 4. Sesiones (2–3 h cada una)
| Sesión | Objetivo y entregables | Acciones tuyas | Criterio de salida |
|---|---|---|---|
| **S13** Spike del runtime | Adapter `Runtime`, `/sessions`, `/tree` crudo y appender de `/logs`. Prueba a mano con la preview de una view en `PDK/_eyes/` | Aprobar un reinicio | Se confirma: formato del address path (¿igual al del DOM?), lectura en `queue()`, calidades por prop, claves del MDC, D6 y D7. **Queda decidido el corte.** |
| **S14** Endpoints de árbol | Forma final de D7, timeouts, views embebidas como instancias aparte, `501` degradado, `runtime` en `/health`. Tests `-m gateway` | Aprobar un reinicio | Árbol de una view de prueba con un tag inexistente: calidad `Bad_NotFound` en el componente correcto. Los tests en vivo pasan |
| **S15** Port del harness | `pdk/eyes/browser.py`: readiness, estabilidad, extracción, consola y captura. Sesión persistente (D9). Tests unitarios de lo puro y `-m gateway` del resto | — | Abrir la preview, quedar estable y extraer todos los `data-component-path` en ~2 s; la captura sale sin el aviso de Maker |
| **S16** Join y reglas | `join.py`, V001–V010 portadas, Q/L/C nuevas, render con presupuesto. Tests con snapshots sintéticos (nada de proyectos reales) | — | Cada regla tiene su caso positivo y negativo; un snapshot sin defectos da 0 anomalías |
| **S17** MCP y salida de F3 | `pdk_open`, `pdk_inspect`, `pdk_screenshot`; CLI; view con defectos sembrados en `PDK/_eyes/Defects`; SKILL.md y AGENTS.md | Mirar el reporte y la captura | Criterio del roadmap (abajo) |
| S18 (colchón) | Correcciones del módulo y prolijidad | Reinicio, si hace falta | — |

Total: unas 12–16 h más el colchón, dentro de las 16–24 h estimadas. El módulo se reinstala 2 veces (3 con el colchón); las sesiones S15–S17 son solo Python.

**Criterio de salida (roadmap).** En una view con cuatro defectos sembrados se detectan los cuatro en el componente correcto, con el reporte dentro de 1.500 tokens:
- label con binding a un tag inexistente → Q001;
- label con texto largo sin wrap en un contenedor fijo → V001 o V002;
- contenedor flex colapsado (basis 0, sin grow) → V007;
- transform de script que lanza una excepción → Q002 (y L001 si el log lo atribuye).

### Resultado de S13 (5 de octubre de 2026)
- **Lo interno enlaza: no hace falta el corte.** `/health` informa `runtime: true` y `logs: true`. `/sessions`, `/tree` y `/logs` funcionan; la lectura en `session.queue()` responde enseguida. La view sembrada (`PDK/_eyes/Defects`) abre en Chromium en 0,6–1,3 s.
- **Address path.** El gateway da `0:1` y el DOM `C.0:1`: el path del DOM es `<mountPath>.<address>`, y la view principal tiene `mountPath` `C`. El join es directo.
- **D6, resuelto.** `sessionStorage["perspective-tab-id"]` es el id de página del gateway (`1e110d67`). El id de sesión que guarda el cliente en `localStorage` **no** coincide con el UUID del gateway, así que se busca la página en las sesiones del proyecto. Las sesiones de navegadores cerrados siguen vivas un rato (2 en la segunda corrida): hay que elegir por id de página, nunca por «la más nueva».
- **D7, resuelto.** `PropertyTree.toJson()` devuelve la forma calificada (`{"$":["qv",código,ts],"$v":valor}`, con `[código, mensaje]` si no es Good): unos 4,5 KB por componente. El módulo tiene que devolver los valores planos y la lista de calidades no-Good. `report()`, `getViewState()` y `getComponentState()` no agregan nada al recorrido propio: se descartan. Bindings: `ViewConfig.root` recorrido en paralelo da tipo, transforms, valor y calidad de cada prop con binding.
- **Calidades, en el componente correcto:**
  - tag inexistente: `Bad_NotFound`, con el mensaje `Path '[default]PDK_Eyes/NoSuchTag' not found.`;
  - transform de script que lanza: `Error_ScriptEval`, con el traceback de Python en el mensaje.

  El error del transform **no se loguea**, así que Q002 sale de la calidad y no de L001. En el DOM, estos labels muestran `null` con un overlay de error (`overlay-error`).
- **D8, resuelto.** Un `onStartup` que lanza se loguea como WARN `perspective.actions.script`. El MDC trae `component: root/Healthy` (la ruta por nombres que ya usa el validador), `view: PDK/_eyes/Defects@C` y `project-name`, pero **no la sesión**. Por eso L001 filtra por proyecto, instancia de view y ventana de tiempo. El appender también captura errores de otros proyectos (un timer de otro proyecto), que se filtran en Python.
- **DOM.** Los 8 componentes tienen `data-component-path`. `Collapsed` mide 0 px de alto (V007) y `Narrow` 120 px con el texto cortado (V001). El aviso de Maker aparece en cada contexto nuevo y tapa la captura: el port del harness lo cierra.
- **Entorno.** Se suma `playwright` 1.63, que usa el Chromium 1243 ya instalado. Mientras corre el MCP `pdk`, `uv sync` no puede reemplazar `pdk-mcp.exe`: después de cambiar dependencias hay que reconectar el MCP.

### Resultado de S14 (5 de octubre de 2026)
- **Módulo:**
  - `GET /pages/{id}?project=` busca la página por id en las sesiones del proyecto y devuelve cada view montada. Por componente trae address, tipo, nombre, ruta por nombres (`root/Row/Narrow`), `props`/`position`/`custom`/`meta` en JSON plano, las calidades no-Good con su path y mensaje, y los bindings configurados.
  - `/sessions` queda compacto.
  - `/logs` filtra por `project` y `view` con el MDC.
  - La conversión de la forma calificada vive en `Qualified.java`, probada en SelfTest.
- **Cliente:**
  - `sessions`, `page` y `logs`;
  - G011 `runtime-unavailable` y G012 `session-busy`;
  - `pdk gateway sessions|logs`;
  - `health` avisa si los ojos quedan degradados.
- **Hallazgo: los arrays calificados usan `$a`, no `$v`.** Lo encontró el test en vivo. Se corrigió en Java (entra en la próxima reinstalación) y, hasta entonces, `Gateway.page()` lo normaliza en Python (`unwrap_arrays`), así que S15–S17 no necesitan reiniciar.
- Los 11 tests `-m gateway` pasan, incluido el nuevo: abre una view en Chromium y verifica `Bad_NotFound`, `Error_ScriptEval`, bindings, el array plano y el log de `onStartup` atribuido al componente.

### Resultado de S15 (5 de octubre de 2026)
- **`pdk/eyes/browser.py`**, port del harness sin IR, hashes ni artefactos:
  - readiness: raíz `C.0` visible, «View Not Found», sesiones rechazadas, banner de desconexión, DPR 1 y fuentes;
  - cierre del aviso de Maker;
  - estabilidad: sin animaciones y 3 muestras rAF iguales, con tolerancia de 0 px;
  - extracción, consola (`error`/`warning`/`pageerror`) y captura del viewport o de un componente.
- **Un hilo propio.** `Eyes` corre Playwright en su propio hilo: la API síncrona no puede usarse dentro del event loop del MCP, y sus objetos quedan atados al hilo que los creó. Verificado llamándolo desde `asyncio.run`. Chromium y un contexto por viewport quedan abiertos: reabrir la misma view recarga la pestaña, mantiene la sesión y la página Perspective (mismo `perspective-tab-id`) y no vuelve a mostrar el aviso de Maker.
- **La extracción, mejorada respecto de compiler:**
  - el texto de cada componente excluye el de sus hijos y el del overlay de calidad;
  - por componente se registra el overlay que dibuja Perspective sobre los bindings malos (`ia_qualityOverlay--error`). Sirve de respaldo en modo solo DOM.
- **Tiempos con la view sembrada:**
  - primera apertura estable en 1,4 s (readiness en 0,5 s);
  - extracción en 0,14 s;
  - reapertura en 0,3 s.

  Los 8 `data-component-path` salen completos: `Collapsed` mide 0 px y `Narrow` tiene `scroll_width` 507 contra `client_width` 120. La captura sale sin el aviso.
- Pasan los tests unitarios de lo puro (viewport, estados terminales y delta de geometría) y los 12 `-m gateway`.

### Resultado de S16 (5 de octubre de 2026)
- **`pdk/eyes/join.py`.** El join reutiliza el árbol del validador, que ya calcula el address path: pointer `/root/children/i` ↔ gateway `0:i` ↔ DOM `C.0:i`.
  - Los componentes de views embebidas (`$`) y docks se cuentan como ajenos.
  - Un tipo distinto entre el `view.json` y la página (o el gateway), o componentes de más en la página, se registran como desincronización.
  - Breakpoint, tabs y `position.display: false` pueden no montar hijos.
- **`pdk/eyes/rules.py`** produce el mismo `Diagnostic` que el validador (`render`, presupuesto y `focus` sin cambios). Los códigos nuevos están en `diagnostics.CODES`:
  - **V001** texto cortado, en el eje que corta, con la elipsis incluida;
  - **V002** desborde de contenedor;
  - **V003** desborde del viewport, en el contribuyente más profundo;
  - **V007** colapsado: cualquier componente con contenido y tamaño 0 dentro de un padre con lugar;
  - **V010** no montado;
  - **Q001/Q002/Q003** calidad Bad/Error/Uncertain, apuntando al binding (`/propConfig/props.text`), con el mensaje del gateway (última línea del traceback) y el config del binding en el detalle;
  - **L001** error de script logueado, apuntando al evento (`/events/system/onStartup`) según el MDC;
  - **C001** consola o excepción del navegador;
  - **X001** ojos degradados (sin árbol: los overlays del DOM reemplazan a Q);
  - **X002** página desincronizada: si aparece, se informa solo eso.
- **Cascadas.** Lo que pasa dentro de un componente colapsado se informa una sola vez, como su V007. Un texto que solo recorta la raíz queda para V003. La tolerancia es 0,5 px para no informar redondeos subpíxel.
- **En vivo, con la view sembrada:** los 5 defectos salen en el componente correcto, en 368 tokens:
  - Q001 en `root/MissingTag`;
  - Q002 en `root/ScriptError`;
  - V007 en `root/Collapsed`;
  - V001 en `root/Row/Narrow`;
  - L001 en `root/Healthy`.
- **20 tests unitarios con snapshots sintéticos:** positivo y negativo por regla, cascadas, X002, ajenos y presupuesto con 300 anomalías (≤1.500 tokens).

### Resultado de S17 (5 de octubre de 2026): F3 cerrada
- **`pdk/eyes/inspect.py`** orquesta todo:
  - resuelve la view (carpeta local, `view.json` o path del sandbox);
  - el `view.json` del join es el del gateway, es decir lo que se renderiza; si la copia local difiere, el reporte avisa que hay ediciones sin desplegar;
  - toma el cursor de logs, abre la preview y lee el árbol, esperando hasta 3 s a que no queden calidades Uncertain;
  - extrae, cruza, aplica las reglas y renderiza con presupuesto.

  Los errores al abrir salen con estado y hint (sesión rechazada por Maker, view inexistente, etc.).
- **CLI:** `pdk open|inspect|screenshot <path> [--viewport] [--focus] [--component] [--out]`. **MCP:** `pdk_open`, `pdk_inspect` y `pdk_screenshot`, todas de solo lectura; la captura vuelve como `ImageContent`. El servidor mantiene un solo `Eyes` durante toda su vida.
- **Hallazgo: D9 corregido.** Recargar la misma pestaña **no** reinicia la view: Perspective conserva la página (mismo `perspective-tab-id`) y sus instancias de view, así que `onStartup` no vuelve a correr y L001 solo se veía la primera vez. Ahora cada apertura es una pestaña nueva en el mismo contexto: una página nueva de la misma sesión, en 0,6 s y sin repetir el aviso de Maker. Las páginas viejas quedan desconectadas hasta que vencen.
- **Docs.** SKILL.md suma el paso 7 («mirala andando»: inspeccionar después de cada deploy, qué significa cada familia y cómo corregirla). AGENTS.md (2.029 bytes) y CLAUDE.md quedan al día.
- **Criterio de salida del roadmap, cumplido.** Test en vivo `test_inspect_finds_every_seeded_defect_on_its_component`, con la view sembrada como fixture sintética en `tests/fixtures/eyes/`. Dos inspecciones seguidas en el mismo navegador encuentran exactamente los 5 hallazgos esperados, cada uno en su componente y pointer, en unos 370 tokens (el presupuesto es 1.500):
  - Q001 en el binding del tag inexistente;
  - Q002 en el transform de script;
  - V001 en el texto desbordado;
  - V007 en el contenedor colapsado;
  - L001 en el `onStartup` que falla.

  Pasan los 13 tests `-m gateway`, los unitarios y ruff.
- **Pendiente para la próxima reinstalación del módulo** (F5 o el colchón): la corrección de `$a` en `Qualified.java`, ya commiteada. Hasta entonces la cubre `unwrap_arrays` en Python.
- **Desvíos respecto del plan:**
  - `pdk_logs` no es una herramienta aparte (L001 va en `pdk_inspect`);
  - el colchón (S18) no hizo falta.

## 5. Riesgos y corte
- **Lo interno no enlaza o no es seguro leerlo** (S13). Corte del roadmap: ojos solo de DOM + consola (+ logs si el appender funciona), y se revalúa el valor del módulo. V/C siguen; Q queda en X001.
- **Address path distinto del DOM.** Si `getComponentAddressPath()` no coincide con `data-component-path`, el join se arma por posición desde la raíz en los dos lados (ambos son índices de hijos).
- **Sesiones en Maker.** Si el límite de sesiones molesta, D9 ya reutiliza una sola; si no alcanza, `pdk_open` cierra la anterior.
- **Bindings asíncronos.** Un binding a tag puede no tener calidad final al momento de leer. Se espera a que no haya calidades `Uncertain_Initial`/pendientes, con techo de unos segundos; lo que quede pendiente sale como Q003.
- **El appender de logs no atribuye por componente.** L001 apunta a la view y el detalle trae la traza; Q002 ya cubre el componente.
- **Fuera de F3:** interacción y simulación (F5: `fireEvent`, escritura de props, tags sandbox), view tests (F6), sesión del Designer, docks y popups.
