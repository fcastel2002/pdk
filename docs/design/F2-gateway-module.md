# F2 — pdk-gateway (manos): plan por sesiones

Borrador del 4 de octubre de 2026, para aprobar antes de escribir código. Fuente: roadmap §9.4, javap sobre los jars instalados (8.3.9 / Perspective 3.3.9), javadoc 8.3.8, `ignition-sdk-examples` rama `ignition-8.3` y `perspective-probe`.

## 1. Hallazgos

### SDK 8.3.9 (firmas leídas de los jars instalados)
- `ResourceCollectionManager.push(PushOperation)`: si un cambio choca con el estado actual lanza `PushException` en el momento. Los casos son CREATE sobre un path existente (`CREATE conflict: '%s' already exists`) y MODIFY o DELETE con firma vieja (`MODIFY illegal: signature mismatch`). Vuelve después de escribir en disco; el `CompletableFuture` se completa cuando los listeners ya fueron notificados.
- Operaciones: `ChangeOperation.newCreateOp(Resource)`, `newModifyOp(Resource, ResourceSignature base)` y `newDeleteOp(ResourceSignature)`. `PushOperation(changes, contextObject[, lockTimeoutMs])`: el context object les llega a los listeners, así que podemos distinguir nuestros push de los del Designer. También existen `pushPartial`, `pull(snapshots)` y `requestScan()`.
- `LastModification.update(resource, "pdk")` deja el actor en `resource.json`, igual que el Designer, que pone `admin`.
- Para escuchar cambios: `ResourceCollectionLifecycleFactory`, filtrada por proyecto y tipo, como en el ejemplo `project-resource`. La alternativa es un `ResourceListener` sobre la colección. Las dos reciben el contexto del push.
- `RouteGroup`: `newRoute(..).method(POST|PUT|DELETE)`, con `accessControl` obligatorio y `RequestContext.readBody()`. El servlet de rutas no tiene filtro CSRF ni de origin (a confirmar en el spike). `getMountPathAlias()` permite exponer `/data/pdk/v1/...`.
- `ComponentDescriptor` expone `schema()`, `childPositionSchema()`, `events()` (nombre, descripción y schema), `extensionFunctions()` y `moduleId()`. El JSON crudo de `JsonSchema` no es público: `getSchemaNode()` es protected, así que va por reflexión detrás del adapter y, si falla, degrada a warning.
- La API REST nativa de 8.3 no hace CRUD de recursos de proyecto: solo list, copy, rename, export, import y delete del proyecto entero. Por eso el módulo hace falta. Sí trae `POST /data/api/v1/modules/upload?fileName=`, `/modules/install?moduleId=` y `/restart-tasks/restart?confirm=true`, con permiso WRITE y header `X-Ignition-API-Token`. Con eso, instalar y reiniciar se puede automatizar sin admin del SO.
- 8.3 no tiene hot reload: cada cambio del módulo implica reiniciar el gateway (el `DeveloperModuleLoadingServlet` ya no existe).
- Designer 8.3.9: muestra «Project Update Available» y aplica los cambios con *Update Project* (Ctrl+Shift+U). Si hay cambios locales en conflicto abre `ConflictResolutionDialog`. No hay bloqueos de edición del lado del gateway, así que la concurrencia se resuelve por firma (optimista).

### Ejemplos oficiales (rama `ignition-8.3`)
- `project-resource` y `immutable-project` usan `io.ia.sdk.modl` 0.1.1, Gradle 8.2.1 / 7.5.1, toolchain Java 17 y `skipModlSigning`.
- La última versión publicada del plugin es la 0.5.0, construida con Gradle 7.6 y JDK 11. `master` ya pasó a Gradle 9.4 / JDK 25, pero esa versión (1.0.0) no está publicada.

### Entorno local
- El único JDK instalado es el 25. El gateway corre sobre su propio Zulu 17.0.19.
- Gradle 8.x no corre sobre JDK 25 (lo soporta desde Gradle 9.1).
- Verificado: `javac --release 17` del JDK 25 compila contra los jars 8.3.9/3.3.9 sin warnings (`-Xlint:all -Werror`), en 0,8 s y con bytecode 61.
- `ignition.conf` ya tiene `-Dignition.allowunsignedmodules=true` y `-Dedition=maker`. El servicio `Ignition` corre como LocalSystem, con inicio manual.
- `user-lib/modules` y `data/` no se pueden escribir sin admin. `data/` es legible por Users, así que el token también lo es (aceptable en desarrollo).
- `pdk_sandbox` está vacío: tiene `page-config` sin páginas, no es heredable y no tiene padre.
- En disco, una view es una carpeta con `resource.json` (scope G, `view.json` + `thumbnail.png`, atributos `lastModification*`). Las carpetas no llevan `resource.json`.

### perspective-probe: qué se reutiliza
`ProbeSecurityFilter` (loopback + comparación en tiempo constante), `ProbeToken`, el helper de rutas con parseo de path comodín, `PerspectiveGatewayAdapter` y la base de `CatalogService`/`ProjectService`. Cambios necesarios:
- token leído de forma perezosa (no depender del orden `mountRouteHandlers`/`startup`);
- verbos de escritura (el probe solo montaba GET);
- paths de view con espacios (la regex del probe los rechazaba);
- id de módulo propio, fuera de `com.inductiveautomation`;
- sin firma.

## 2. Decisiones (a confirmar)
| # | Decisión | Recomendación | Alternativa |
|---|---|---|---|
| D1 | Build | Script Python: `javac --release 17` contra los jars instalados y armado del `.modl` sin firmar. No necesita descargas y compila exactamente contra 8.3.9/3.3.9. **Se aparta del roadmap** («armado con io.ia.sdk.modl»). | Wrapper Gradle 9.x con plugin `java` y zip propio, como el probe (descarga unos 130 MB; da integración con el IDE). El plugin 0.5.0 no está verificado en Gradle 9. |
| D2 | Sandbox | Carpeta `PDK/` dentro de `pdk_sandbox`. Promover es pasar de `PDK/X` a `X` **dentro del mismo proyecto**, siempre con firma. | Proyecto hijo heredado. Implica crear un proyecto y volver heredable a `pdk_sandbox`. |
| D3 | Flag de escritura | `-Dpdk.sandbox=pdk_sandbox` en `ignition.conf`. Sin esa propiedad el módulo es de solo lectura. Funciona a la vez como flag de desarrollo y como nombre del sandbox. | Flag en runtime por endpoint. No suma seguridad: quien tiene el token puede cambiarlo. |
| D4 | Instalación | REST de 8.3 con una API key de escritura que creás una vez. El script la lee de `PDK_IGNITION_API_KEY`. El reinicio te lo pido siempre. | Subida manual desde la web del gateway en cada versión. |
| D5 | MCP | `pdk_pull`, `pdk_deploy`, `pdk_promote` y además `pdk_delete`, separada y marcada como destructiva. | Borrar solo desde la CLI. |

Promover a otros proyectos queda fuera de F2 por la regla de no tocarlos. Si más adelante hace falta un destino real, se habilita con una allowlist explícita.

## 3. Diseño

### Módulo (Java 17, fino)
id `dev.pdk.gateway`, alias de montaje `pdk` → base `/data/pdk/v1`. Gratis y compatible con Maker; depende de Perspective (`required`).

| Método | Ruta | Qué hace | Requiere |
|---|---|---|---|
| GET | `/health` | Versiones, edición, sandbox y si la escritura está habilitada | token |
| GET | `/catalog` · `/catalog/{id}` | Resumen de componentes registrados y descriptor completo con la forma de `ia.components.json`, más `moduleId` | token |
| GET | `/projects/{p}/resources/{módulo}/{tipo}/{path…}` | Archivos, firma y `lastModification`; si es carpeta, el listado | token |
| PUT | ídem, body `{files, signature\|null}` | CREATE si `signature` es null, MODIFY con la firma esperada. Conserva los demás archivos (thumbnail). Espera a los listeners. 409 con el estado actual | token + escritura + proyecto == sandbox |
| DELETE | ídem `?signature=` | DELETE con firma; 409 si no coincide | ídem |
| POST | `/scan` | `requestScan()` y espera | token + escritura |
| GET | `/changes?since=N` | Eventos del listener (buffer en memoria, sin persistir): origen pdk o externo, actor y firma | token |

Seguridad:
- Solo loopback.
- Token aleatorio en `data/pdk-gateway.token`, nuevo en cada arranque, enviado en el header `X-PDK-Token`.
- Cada escritura deja una línea INFO en el log del gateway.
- El 409 devuelve `{code, current:{signature, files, lastModification}}`.

### Python (la lógica)
- `pdk/gateway/client.py`: HTTP con stdlib, descubrimiento del token y errores tipados con códigos estables `G0xx`, pointer y hint.
- `pdk/gateway/module.py`: `build`, `install` y `restart` (espera RUNNING y verifica `/health`).
- `pdk/sync.py`:
  - **pull** baja a `work/` (en `.gitignore`) con un sidecar `.pdk-base.json` que guarda proyecto, path, firma y el contenido base;
  - **deploy** corre `pdk validate`, rechaza si hay errores y hace el PUT;
  - **promote** y **delete** usan la firma esperada que quedó en el sidecar;
  - ante un 409 arma un diff por JSON pointer (tu versión contra el gateway, y lo que cambió el Designer desde tu pull), dentro del presupuesto de tokens.
- `pdk/live.py`: `pdk schemas sync` escribe los componentes de terceros en `components/live.components.json`, que el loader ya lee sin cambios. Además informa el drift de los 70 built-in contra el bundle offline.
- CLI: `pdk gateway build|install|restart|health|scan|changes`, `pdk pull|deploy|promote|delete`, `pdk schemas sync`.
- MCP: las herramientas de D5, anotadas; promote y delete como destructivas.

## 4. Sesiones (2–3 h cada una)
| Sesión | Objetivo y entregables | Acciones tuyas | Criterio de salida |
|---|---|---|---|
| **S7** Andamiaje, build y seguridad (+ push crudo) | `gateway/`: hook, token, filtro, adapter y guarda de sandbox. `pdk gateway build/install/restart/health`. `/health`, lectura de recursos y PUT/DELETE crudos para el spike | API key (D4), editar `ignition.conf` (D3), aprobar el reinicio | 200 con token, 401 sin token, 403 desde la IP de LAN. El token cambia al reiniciar. Sin la propiedad, la escritura se rechaza. `PDK/Spike`: crear → modificar → firma vieja da conflicto → borrar. **Queda decidido el corte.** |
| **S8** Push y conflictos | API de recursos completa: carpetas, 409 con estado, `LastModification` y espera del future. `pdk pull/deploy/delete`, diff del 409. Tests unitarios con transporte falso y de integración `-m gateway` | Reinicio. Editar y guardar en el Designer durante la prueba | Crear, editar y borrar una view sin el Designer. Una edición guardada en el Designer devuelve 409 con diff y el gateway queda intacto |
| **S9** Scan, listener y catálogo vivo | `/scan`, `/changes` (origen pdk/externo), `/catalog`. `pdk schemas sync` y aviso si el bundle y el gateway tienen versiones distintas | Reinicio | Drift 0/70 en built-in. Una edición del Designer aparece como externa en `/changes` |
| **S10** Sandbox, promote y preview (solo Python) | Convención `PDK/`. `pdk promote`. Ruta `/pdk/<path>` en `page-config` con firma y reintento aditivo ante 409. URL de preview en la salida del deploy | — | Promover una view nueva. Promover sobre un destino editado por vos da 409. La URL de preview abre la view |
| **S11** MCP, skill y salida de F2 | `pdk_pull/deploy/promote/delete`, presupuesto ≤1.500 tokens. Actualizar el flujo de SKILL.md y los comandos de AGENTS.md/CLAUDE.md | Recorrer los criterios con el Designer abierto | Los tres criterios del roadmap, en las dos direcciones del conflicto. El Designer refleja los cambios del agente después de *Update Project* |
| S12 (colchón) | Correcciones del módulo (4.º reinicio) y prolijidad | Reinicio | — |

Total: unas 12–16 h más el colchón, dentro de las 16–24 h estimadas. El módulo se reinstala 3 o 4 veces; por eso cada sesión con Java agrupa todos sus cambios.

### Resultado de S7 (4 de octubre de 2026)
- Módulo instalado y corriendo (`/data/pdk/v1`). Los 5 tests en vivo pasan: 401 sin token, 403 desde la IP de LAN, escritura rechazada fuera del sandbox y el ciclo crear → modificar → firma vieja da 409 → crear sobre algo existente da 409 → borrar con firma vieja da 409 → borrar.
- **No hace falta el corte: push funciona desde el módulo.** Las views anidadas se crean sin recursos de carpeta (en el modelo, las carpetas no son recursos). El `resource.json` queda con el mismo formato que los del Designer y `lastModification.actor = "pdk"`. El future de listeners se completa antes de responder.
- Pendiente para S8: al borrar quedan directorios vacíos en disco (`PDK/_selftest`). Falta además probar con el Designer abierto.
- Instalación: cada upload borra la aceptación del certificado. `pdk gateway install` la repite por REST (`POST /modules/certificate`) antes del reinicio. Si no se hace, el gateway frena en el comisionamiento y deja el módulo deshabilitado; aceptarlo ahí no alcanza para un módulo sin firmar.

### Resultado de S8 (4 de octubre de 2026)
- **Carpetas.** El gateway crea las carpetas de una view nueva como recursos implícitos y no las borra al borrarla. Ahora el módulo, después de cada DELETE, borra por `push` las carpetas que quedaron vacías, de la más profunda hacia arriba, y la respuesta las lista en `pruned`. El directorio en disco desaparece con el recurso. El barrido de disco queda solo como resguardo. Las carpetas vacías de S7 se limpiaron.
- **Python.** `pdk pull|deploy|delete` (`pdk/sync.py`):
  - copia en `work/<proyecto>/<path>/` con el sidecar `.pdk-base.json` (firma y texto base);
  - `deploy` valida antes de escribir y crea si no hay sidecar;
  - ante un 409 arma el diff por JSON pointer en tres partes (gateway desde el pull, tuyo, ambos), guarda `view.gateway.json` y pide `--resolved` para escribir encima;
  - `pull` no pisa ediciones locales sin `--force`.
- **Prueba con el Designer abierto** (`PDK/Demo`). Una edición guardada en el Designer dio 409 con diff y el gateway quedó intacto. El merge con `--resolved` conservó el thumbnail del Designer, y el Designer mostró los cambios del agente después de *Update Project*. El borrado podó `PDK`. Los 7 tests `-m gateway` pasan.
- **Hallazgo.** El primer guardado del Designer reescribe `view.json` con las claves ordenadas y agrega `thumbnail.png`, aunque el contenido no cambie. La firma cambia y el deploy da 409, y el diff lo informa como «solo formato u otros archivos». Pulsar Enter en el editor de propiedades antes de Ctrl+S: si no, la edición no entra en el guardado.
- **Instalación.** La reinstalación con reinicio terminó en RUNNING sin pasar por el comisionamiento: `install` vuelve a aceptar el certificado en cada upload.

### Resultado de S9 (4 de octubre de 2026)
- **Módulo:**
  - `/changes` es una `ResourceCollectionLifecycleFactory` limitada al sandbox y a views, page-config, style-classes y session-props. Guarda las últimas 1000 operaciones en memoria, cada una con su origen, el actor y la firma.
  - `/scan` llama a `requestScan()` y espera.
  - `/catalog` (con `?full=true`) y `/catalog/{id}` devuelven los descriptores registrados con la forma de `ia.components.json`. El schema crudo se lee por reflexión sobre `getSchemaNode()`, y funciona.
- **Hallazgo: el contexto llega envuelto.** La factory recibe el context object de nuestro push dentro de un `Optional`, y hubo que desenvolverlo (un reinicio extra). El guardado del Designer no trae context object, así que se marca como externo.
- **Python:** `pdk gateway scan|changes`. `pdk schemas sync` compara el catálogo vivo con el bundle offline y escribe en `components/live.components.json` los componentes que el bundle no trae. Si la versión del gateway no tiene bundle, lo rechaza.
- **Drift 0/70 built-in y 0/11 del resto empaquetado** (81 registrados, 0 de terceros). Schemas, posiciones de hijos y eventos coinciden en todos. El registro completa la metadata que el archivo omite (`name`, `defaultMetaName`, categoría `unknown`) y marca como deprecated los componentes con `showOnPalette: false`, así que esa metadata se compara solo donde el archivo la declara.
- **Criterio:** una edición guardada en el Designer aparece como `modified views/PDK/Demo [external by admin]`. Los 9 tests `-m gateway` pasan.

### Resultado de S10 y S11 (4 de octubre de 2026): F2 cerrada
- **S10 (solo Python):**
  - `pdk promote` copia `PDK/X` a `X` del sandbox con la firma del destino. Esa firma la guarda el sidecar de `work/<proyecto>/X`.
  - Cada deploy mapea `/pdk/<view path>` (con los espacios como guiones) en `page-config` y devuelve la URL de preview. La edición es aditiva, con firma y reintento ante 409, y `delete` saca la página.
  - La URL abre la view en el navegador. En Maker aparece encima el aviso «Personal Use Only».
- **S11.** Herramientas MCP `pdk_pull`, `pdk_deploy`, `pdk_promote` y `pdk_delete`; las dos últimas van anotadas como destructivas. SKILL.md suma el paso de publicación, y AGENTS.md (menos de 2 KB) y CLAUDE.md quedan al día. Pasan los tests unitarios y los 10 de `-m gateway`.
- **Recorrido con el Designer abierto:**
  - **Designer → agente.** Un deploy sobre una view guardada en el Designer da 409 con diff (S8). Promover sobre un `Demo` editado en el Designer también da 409, y el destino queda intacto.
  - **Agente → Designer.** Con un cambio sin guardar en `PDK/Demo`, el agente hace deploy. Al guardar, el Designer abre su diálogo de conflicto; eligiendo «Gateway» queda la versión del agente. Con *Update Project* el Designer refleja los cambios.
  - Un guardado del Designer también puede reescribir `page-config` (formato), y las páginas de preview se conservan.
- **Criterios de salida del roadmap, cumplidos:** crear, editar y borrar sin el Designer; una edición concurrente da 409 y no se pisa; el Designer refleja los cambios del agente.
- **Desvío respecto del roadmap:** el build es con `javac` y no con `io.ia.sdk.modl` (D1). Quedan fuera, como estaba previsto, promover a otros proyectos y detectar views abiertas en el Designer.

## 5. Riesgos y corte
- **Push no usable desde el módulo** (S7). Corte: el módulo escribe el archivo, porque corre como SYSTEM, y llama a `requestScan()`, solo sobre recursos que antes creó el Designer.
- **Carpetas intermedias.** Puede que haga falta crear recursos carpeta explícitamente; se resuelve en el spike.
- **Instalación de un módulo sin firmar por REST.** Confirmado en S7: aun con `allowunsignedmodules`, la primera instalación queda en cuarentena («certificate not yet accepted») y el gateway arranca en la pantalla de comisionamiento hasta que alguien acepta el módulo en la web. A verificar en la próxima reinstalación si las actualizaciones ya no lo piden.
- **La reflexión sobre `getSchemaNode()` deja de enlazar.** El catálogo vivo degrada a warning y queda el bundle offline.
- **El Designer no refleja los cambios solo.** El criterio es que lo haga después de *Update Project*, que es el comportamiento de 8.3.9.
- **Fuera de F2:**
  - detectar views abiertas en el Designer (`DesignerConcurrencyManager` es interno y no tiene getters);
  - promover a otros proyectos;
  - sesiones y árboles (F3).
