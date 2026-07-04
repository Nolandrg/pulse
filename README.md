# Pulse

*[Leer en español](./README.es.md)*

Lightweight self-hosted dashboard for checking and notifying updates on Docker containers and self-hosted applications.

Pulse exists to replace combinations that are heavier than necessary for a simple task: knowing whether a newer version of your containers is available, without running a headless browser just to keep a Distill-style extension watching release pages, and without the inconsistencies that tools like Watchtower or WUD have when it comes to correctly identifying the actual latest version of each image.

## Screenshot

![Pulse dashboard](./docs/screenshot.png)

## What it does

- Watches images on **Docker Hub**, **GHCR** (and generic OCI registries) and **GitHub repos** from a single dashboard.
- Automatically detects how to compare each service's version (semver, semver with a known build suffix like LinuxServer's, date-based versioning, digest of a pinned image, or a floating tag like `latest`/`release`), instead of applying one generic heuristic to everything.
- Automatically imports the containers you already have running by reading the Docker socket, and excludes itself.
- Syncs the installed version with the real running container before every check, so if you update something on your own (e.g. through Portainer), Pulse picks it up automatically.
- Sends Telegram notifications when a real update is available.
- Traffic-light status on the dashboard:
  - 🟢 Green — up to date
  - 🟡 Yellow — update available
  - 🔴 Red — check failed (or not checked yet)
  - ⚪ Gray — checking paused for that service
- Configurable check interval and time window, right from the dashboard.
- Built to be resource-light: no browser, no heavy dependencies.

## Installation

Pulse doesn't publish a prebuilt image yet — it's built locally with Docker Compose.

1. Clone the repository:

   ```bash
   git clone https://github.com/Nolandrg/pulse.git
   cd pulse
   ```

2. Copy the example environment file and fill in your own values:

   ```bash
   cp .env.example .env
   ```

3. Start the container:

   ```bash
   docker compose up -d --build
   ```

4. Open the dashboard at `http://<your-server>:8060`.

## Configuration

Environment variables (set in your `.env` or directly in `docker-compose.yml`):

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | No | Your Telegram bot token, used to send update notifications |
| `TELEGRAM_CHAT_ID` | No | Chat/user ID that receives the notifications |
| `TZ` | No | Container timezone (e.g. `Europe/Madrid`) |
| `DEFAULT_INTERVAL` | No | Default check interval in minutes, used only if none is saved yet (defaults to 30) |
| `GITHUB_TOKEN` | No | Personal GitHub token, raises the rate limit from 60 to 5000 requests/hour. Can also be set later from the dashboard, under Config |

Everything else (interval, check time window, watched services) is managed from the web dashboard, not from a file. `config/servicios.json` is created automatically on first run and is not pushed to the repository (it's in `.gitignore`, since it may contain your GitHub token if you save one from the dashboard).

## Basic usage

- **Importar (Import)**: scans the containers currently running on the host and adds them to the dashboard automatically.
- **Añadir Servicio (Add Service)**: manually registers a service that isn't a local container (for example, a program installed directly on the system).
- Click a service's **name** to go to its source page (GitHub repo, or Docker Hub/GHCR tags).
- Click a service's **LED** to force an immediate check for that service.

## License

Pulse is distributed under **AGPL-3.0 with the Commons Clause** added. In short:

- You may use, modify and redistribute Pulse freely, including as part of a larger project (e.g. a Linux distribution).
- If you modify Pulse and offer it over a network (even for free), you're required to publish the source code of your changes.
- You may not sell Pulse, offer it as a paid service, or redistribute a commercial product whose primary value comes from Pulse, without the author's express permission.
See the [`LICENSE`](./LICENSE) file for the full legal text.

## Author

David Rebollo García ([@Nolandrg](https://github.com/Nolandrg))
  Questions, suggestions or bug reports are welcome through GitHub Issues.
