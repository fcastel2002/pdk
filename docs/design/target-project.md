# Plan: proyecto destino (`-Dpdk.target`)

## Objetivo
Que las pantallas terminadas lleguen a un proyecto **final** del gateway de desarrollo, que es un calco de producción (Export → Import), sin que ese proyecto reciba nada del trabajo intermedio: ni la carpeta `PDK/` ni las páginas de preview `/pdk/...`.

## Roles
| Propiedad | Proyecto | Qué admite |
|---|---|---|
| `-Dpdk.sandbox=pdk_sandbox` | sandbox | Todo lo de hoy: pull, deploy, promote interno, delete, preview, selftests. |
| `-Dpdk.target=<proyecto_final>` | destino (opcional) | Solo escritura y borrado de **views fuera de `PDK/`**. Sin page-config, sin scan, sin preview. |

Si no hay `target`, todo funciona igual que ahora. `target` no puede ser igual a `sandbox` (el módulo lo rechaza al arrancar y lo informa `/health`).

## Flujo
1. `pdk pull|deploy` en `work/<sandbox>/PDK/<nombre>`: se itera con preview, `inspect` y `screenshot` como hasta ahora.
2. `pdk promote work/<sandbox>/PDK/<nombre> --to-project target [--to <ruta>]`: copia la view **tal como está en el gateway** a `<proyecto_final>:<ruta>`, con la firma del destino y el 409 de siempre si alguien la cambió.
3. La copia local del destino queda en `work/<proyecto_final>/<ruta>` con su sidecar, para el próximo promote o un `pull` del destino.
4. La versión final se revisa en el Designer (o con `pdk open` sobre una página que exista en el destino). Después: Export → producción.

`promote` sin `--to-project` sigue siendo `PDK/X` → `X` dentro del sandbox.

## Cambios

### Módulo Java (requiere reinstalar y reiniciar el gateway)
- `Sandbox.java` → incorporar `target`: `fromSystemProperties()` lee `pdk.target`; nuevos `target()`, `requireViewWritable(project, type, path)`.
  - sandbox: cualquier recurso, como hoy.
  - target: solo `com.inductiveautomation.perspective/views`, path que no sea `PDK` ni empiece con `PDK/`; si no, `403 OUTSIDE_TARGET`.
  - cualquier otro proyecto: `403 OUTSIDE_SANDBOX` (sin cambios).
- `Resources.write/delete`: usar la regla nueva. `scan()` y page-config siguen exigiendo el sandbox.
- `Health`: agregar `target`, `targetExists` y `targetConflict` (`target == sandbox`).
- `GatewayHook`: loguear el target al arrancar.
- `SelfTest`: casos de target permitido, `PDK/` en target, page-config en target y proyecto ajeno.

### Python
- `pdk/gateway/client.py`: `health()` ya trae los campos; agregar helper `target()`.
- `pdk/sync.py`:
  - `promote(..., to_project=None)`: `"target"` (o el nombre literal) resuelve contra `/health`. Lee la view del sandbox y hace `deploy` en `local_dir(target, to)` **sin `ensure_preview`**: `deploy` recibe `preview=False`.
  - El aviso de referencias a `PDK/...` pasa a **error** cuando el destino es el target: una view final no puede apuntar a views del sandbox. También avisar si referencia views que no existen en el target (embeds, popups, `navigate`).
  - `delete` en el target: permitido (con firma), sin `remove_preview`.
- `pdk/cli.py` y `pdk/mcp_server.py`: `--to-project` / `to_project` en promote; `pdk gateway health` muestra el target.
- `pdk/doctor.py`: avisa si `target` no existe o coincide con el sandbox.

### Tests
- `tests/test_sync.py`: promote entre proyectos con un gateway falso (firma del destino, 409, sin preview, referencias a `PDK/`).
- `tests/test_gateway_live.py` (`-m gateway`): solo si el gateway de pruebas tiene un target de selftest; si no, `skip`. Nunca escribir en el proyecto final desde los tests.

### Docs
- `README.md`: la línea de `ignition.conf` con `-Dpdk.target` y el flujo de arriba.
- `AGENTS.md` / `CLAUDE.md`: el target solo se toca con `promote` y siempre con OK explícito del usuario.
- Skill `perspective-views`: paso final de promote al target.

## Riesgos
- **Reimport desde producción**: cambia las firmas del target; el próximo promote da 409 con diff. Es lo esperado: se mergea y `--resolved`.
- **Recursos que no son views** (estilos, scripts, tags, named queries): el target no los recibe por PDK. Si una pantalla los necesita, se crean en el Designer. Queda fuera de alcance a propósito.
- **Preview en el target**: no existe. Si hace falta mirarla en el destino, es con una página real del proyecto o en el Designer.

## Orden
1. Java + SelfTest → `pdk gateway build` (sin instalar).
2. Python + tests unitarios (`uv run pytest`, `uv run ruff check .`).
3. **OK del usuario** → `pdk gateway install --restart` con `-Dpdk.target` en `ignition.conf`.
4. `uv run pytest -m gateway` y un promote real de prueba a una ruta descartable del target, revisada y borrada en el Designer.
