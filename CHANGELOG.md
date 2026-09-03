# Registro de cambios

Todos los cambios notables de este proyecto se documentan en este archivo.

El formato sigue las convenciones de [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/),
y este proyecto usa [Versionado Semántico](https://semver.org/lang/es/).

## [1.2.1] - 2026-09-03

### Corregido

- Corregida la resolución de versiones de Immich en GHCR: Pulse usa la etiqueta estable `release` y la versión OCI publicada por Immich, evitando interpretar el listado lexicográfico de tags como SemVer.

## [1.1.0] - 2026-08-03

### Añadido

- Vista responsive para móvil: layout en tarjetas apiladas, LED y botones de control ampliados para uso táctil, y confirmación antes de eliminar un servicio.

### Corregido

- `resolver_version_mas_reciente` ahora pagina la consulta a Docker Hub (hasta 5 páginas) cuando el tag estable no aparece en los primeros 50 resultados ordenados por fecha. Repos muy activos (ej. `jellyfin/jellyfin`, con builds nightly/rc multi-arquitectura) podían empujar el tag real fuera de esa ventana, dejando el LED en rojo de forma permanente sin ningún error visible en los logs.
- Corregido el patrón de sufijo de LinuxServer (`patron_sufijo`) en `servicios.json`: faltaba el grupo de captura (`-ls\d+$` en vez de `-ls(\d+)$`), lo que impedía detectar subidas de número de build cuando la versión base no cambiaba (ej. `0.6.26-ls390` → `0.6.26-ls393` se mostraba como al día en vez de como actualización disponible).

## [1.0.0] - 2026-07-03

### Añadido

- Panel web con estado por semáforo (verde/amarillo/rojo/gris) para cada servicio vigilado.
- Comprobación de versiones contra Docker Hub, GHCR (y registries OCI genéricos) y GitHub Releases/Tags.
- Motor de comparación con modo explícito por servicio: `semver`, `semver_suffix`, `date`, `digest` y `floating`, con detección automática al añadir o importar un servicio.
- Resolución de versión más alta real en GitHub (a partir de la lista de tags), en vez de fiarse de `releases/latest`, que puede devolver un parche antiguo en vez de la versión mayor.
- Exclusión automática de prereleases (beta, alpha, rc y formas cortas como `-b.88`), tags "canal"/puntero sin parche (`v3`, `8`, `16`...) y timestamps de build nightly incrustados en el tag.
- Resolución por digest para tags flotantes (`latest`, `release`, `stable`...), evitando devolver nombres de build de desarrollo/staging al azar.
- Importación automática de contenedores en marcha vía socket de Docker, con auto-exclusión del propio Pulse.
- Sincronización de la versión instalada con el contenedor real en cada comprobación, para detectar actualizaciones hechas por fuera de Pulse (Portainer, Dockge, manualmente...).
- Notificaciones por Telegram cuando cambia el estado a "actualización disponible".
- Ventana horaria y frecuencia de comprobación configurables desde el panel.
- Soporte de token personal de GitHub opcional, para subir el límite de peticiones de 60 a 5000/hora.
- Enlace directo desde el nombre de cada servicio a su repositorio de GitHub o página de tags del registro correspondiente.
- Licencia AGPL-3.0 con Commons Clause.
