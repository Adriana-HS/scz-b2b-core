"""Orquesta una corrida de carga o de caos del webhook contra el Postgres LOCAL (#21).

    uv run python -m loadtest.run load                # 50 rps durante 3 min: mide el p99 (Q-PERF-1)
    uv run python -m loadtest.run chaos-db            # reinicia Postgres en medio de la carga (Q-REL-1)
    uv run python -m loadtest.run chaos-api           # mata el proceso api (SIGKILL) y lo vuelve a levantar

Pasos: crea un negocio y un número sintéticos con IDs de esta corrida; habilita el login de
`app_backend` con una contraseña aleatoria; levanta la api con uvicorn; calienta el pool;
lanza k6 (imagen oficial fijada por digest); en caos, inyecta la falla a un tercio de la
corrida; al final cuenta en la BD y decide con loadtest/verdict.py. Sale con 0 si pasa.

Solo corre contra una BD local (el mismo guardia que las pruebas de integración).
"""

import argparse
import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg
import httpx

from loadtest.verdict import DbCounts, K6Result, Mode, evaluate, render_markdown

BACKEND = Path(__file__).resolve().parents[1]
K6_SCRIPT_DIR = Path(__file__).resolve().parent / "k6"
# grafana/k6:1.3.0 por digest (se actualiza a propósito, en su propio PR)
K6_IMAGE = "grafana/k6@sha256:3ddc8b1a33a2c3d8edc6e99b6a762ae36cba08788463458f5e6a7703e14eb77d"
ADMIN_DSN = os.environ.get(
    "SCZ_TEST_DATABASE_ADMIN_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)


@dataclass(frozen=True)
class Run:
    mode: Mode
    run_id: str
    org_id: UUID
    phone_number_id: str
    webhook_secret: str
    backend_password: str
    port: int
    rate: float
    duration: str
    out_dir: Path

    @property
    def wamid_prefix(self) -> str:
        return f"wamid.load.{self.run_id}."


def _require_local(dsn: str) -> None:
    host = urlsplit(dsn).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(
            "SCZ_TEST_DATABASE_ADMIN_URL debe apuntar a una BD local, nunca a staging o producción"
        )


def _backend_dsn(password: str) -> str:
    parts = urlsplit(ADMIN_DSN)
    host = parts.hostname or "127.0.0.1"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(parts._replace(netloc=f"app_backend:{password}@{host}{port}"))


def _duration_s(duration: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600}
    return int(float(duration[:-1]) * units[duration[-1]]) if duration[-1] in units else int(duration)


# --- BD ---------------------------------------------------------------------------------------
async def _prepare_db(run: Run) -> None:
    conn = await asyncpg.connect(ADMIN_DSN, timeout=10)
    try:
        literal = await conn.fetchval("SELECT quote_literal($1)", run.backend_password)
        await conn.execute(f"ALTER ROLE app_backend WITH LOGIN PASSWORD {literal}")
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO public.organizations (id, name, vertical)"
                " VALUES ($1, $2, 'IMPORTADORA_REPUESTOS')",
                run.org_id,
                f"Carga {run.run_id}",
            )
            await conn.execute(
                """INSERT INTO public.whatsapp_channels
                       (organization_id, phone_number_id, waba_id, display_phone, access_token_ref)
                   VALUES ($1, $2, 'WABA-LOAD', '+59100000000', 'WA_TOKEN_LOAD')""",
                run.org_id,
                run.phone_number_id,
            )
    finally:
        await conn.close()


async def _count(run: Run) -> DbCounts:
    conn = await asyncpg.connect(ADMIN_DSN, timeout=30)
    try:
        prefix = run.wamid_prefix + "%"
        row = await conn.fetchrow(
            "SELECT count(*) AS n, count(DISTINCT wa_message_id) AS d FROM public.messages"
            " WHERE organization_id = $1 AND wa_message_id LIKE $2",
            run.org_id,
            prefix,
        )
        assert row is not None  # noqa: S101 - un count(*) siempre devuelve una fila
        events = await conn.fetchval(
            "SELECT count(*) FROM private.inbound_events"
            " WHERE organization_id = $1 AND wa_message_id LIKE $2",
            run.org_id,
            prefix,
        )
        without_job = await conn.fetchval(
            """SELECT count(*) FROM public.conversations c
                WHERE c.organization_id = $1
                  AND EXISTS (SELECT 1 FROM public.messages m
                               WHERE m.conversation_id = c.id AND m.wa_message_id LIKE $2)
                  AND NOT EXISTS (SELECT 1 FROM private.jobs j
                                   WHERE j.kind = 'PROCESS_CONVERSATION'
                                     AND j.conversation_key = c.id::text)""",
            run.org_id,
            prefix,
        )
    finally:
        await conn.close()
    return DbCounts(
        messages=row["n"], distinct_messages=row["d"], events=events, conversations_without_job=without_job
    )


async def _db_round_trip_ms(run: Run, samples: int = 30) -> float:
    """Mediana de un `SELECT 1` como app_backend. Cada request del webhook hace ~9 viajes a la
    BD, así que su latencia es ~9 veces este valor más el trabajo de Postgres: explica el p99."""
    conn = await asyncpg.connect(_backend_dsn(run.backend_password), timeout=10, statement_cache_size=0)
    try:
        timings = []
        for _ in range(samples):
            started = time.perf_counter()
            await conn.fetchval("SELECT 1")
            timings.append((time.perf_counter() - started) * 1000)
    finally:
        await conn.close()
    return sorted(timings)[samples // 2]


async def _disable_backend_login() -> None:
    with contextlib.suppress(OSError, asyncpg.PostgresError):
        conn = await asyncpg.connect(ADMIN_DSN, timeout=10)
        try:
            await conn.execute("ALTER ROLE app_backend WITH NOLOGIN PASSWORD NULL")
        finally:
            await conn.close()


# --- api ------------------------------------------------------------------------------------------
class Api:
    """El proceso api real (uvicorn), como en producción: un proceso, sin reload."""

    def __init__(self, run: Run) -> None:
        self._run = run
        self._process: subprocess.Popen[bytes] | None = None
        self._log = (run.out_dir / "api.log").open("ab")
        # Solo loopback: Docker Desktop llega por host.docker.internal y en Linux k6 usa la red del host
        self.host = "127.0.0.1"

    def start(self) -> None:
        env = {
            **os.environ,
            "ENVIRONMENT": "test",
            "LOG_LEVEL": "INFO",
            "DATABASE_URL": _backend_dsn(self._run.backend_password),
            "META_APP_SECRET": self._run.webhook_secret,
            "META_WEBHOOK_VERIFY_TOKEN": secrets.token_urlsafe(16),
            "PYTHONUNBUFFERED": "1",
        }
        env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)  # una corrida local no exporta telemetría
        env.pop("SENTRY_DSN", None)
        command = [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", self.host, "--port", str(self._run.port), "--no-access-log",
        ]  # fmt: skip
        self._process = subprocess.Popen(command, cwd=BACKEND, env=env, stdout=self._log, stderr=self._log)  # noqa: S603

    def kill(self) -> None:
        """SIGKILL (TerminateProcess en Windows): sin apagado ordenado, como una caída real."""
        if self._process is not None:
            self._process.kill()
            self._process.wait(timeout=30)
            self._process = None

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        self._log.close()

    async def wait_healthy(self, timeout_s: float = 60) -> None:
        deadline = time.monotonic() + timeout_s
        async with httpx.AsyncClient() as client:
            while time.monotonic() < deadline:
                with contextlib.suppress(httpx.HTTPError):
                    if (await client.get(self.url("/v1/health"), timeout=2)).status_code == 200:
                        return
                await asyncio.sleep(0.25)
        raise SystemExit(
            f"la api no respondió /v1/health en {timeout_s:.0f} s (ver {self._run.out_dir / 'api.log'})"
        )

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._run.port}{path}"


def _signed(run: Run, wamid: str) -> tuple[bytes, dict[str, str]]:
    value = {
        "metadata": {"phone_number_id": run.phone_number_id},
        "messages": [
            {"from": "59170000000", "id": wamid, "timestamp": str(int(time.time())), "type": "text",
             "text": {"body": "calentando"}}
        ],
    }  # fmt: skip
    body = json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [{"id": "W", "changes": [{"field": "messages", "value": value}]}],
        }
    ).encode()
    signature = "sha256=" + hmac.new(run.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": signature, "Content-Type": "application/json"}


async def _warm_up(run: Run, api: Api, requests: int = 100) -> None:
    """Calienta el pool y el import perezoso de módulos; estos mensajes no cuentan (otro prefijo)."""
    async with httpx.AsyncClient() as client:
        for i in range(requests):
            body, headers = _signed(run, f"wamid.warm.{run.run_id}.{i}")
            response = await client.post(api.url("/v1/webhooks/whatsapp"), content=body, headers=headers)
            if response.status_code != 200:
                raise SystemExit(
                    f"el calentamiento falló con {response.status_code}: revisar {run.out_dir / 'api.log'}"
                )


# --- k6 y caos ------------------------------------------------------------------------------------
def _docker_needs_host_gateway() -> bool:
    """En Linux, k6 usa la red del host. En Docker Desktop (Windows/macOS), host.docker.internal."""
    return not sys.platform.startswith("linux")


def _k6_command(run: Run) -> tuple[list[str], dict[str, str]]:
    target = f"http://{'host.docker.internal' if _docker_needs_host_gateway() else '127.0.0.1'}:{run.port}"
    env = {
        "TARGET": target,
        "WEBHOOK_SECRET": run.webhook_secret,
        "PHONE_NUMBER_ID": run.phone_number_id,
        "RUN_ID": run.run_id,
        "RATE": f"{run.rate:g}",
        "DURATION": run.duration,
        "SUMMARY_PATH": "/out/summary.json",
    }
    command = ["docker", "run", "--rm", "-v", f"{run.out_dir}:/out", "-v", f"{K6_SCRIPT_DIR}:/scripts:ro"]
    if not _docker_needs_host_gateway():
        command += ["--network", "host"]
    if sys.platform != "win32":
        # La imagen de k6 corre con su propio usuario: sin esto no puede escribir el resumen en
        # la carpeta montada (en Linux los permisos se respetan; Docker Desktop los ignora)
        command += ["--user", f"{os.getuid()}:{os.getgid()}"]
    for name in env:
        command += ["-e", name]  # solo el nombre: el valor sale del entorno, no de la línea de comandos
    command += [K6_IMAGE, "run", "--quiet", "/scripts/webhook.js"]
    return command, env


def _db_container() -> str:
    names = subprocess.run(
        ["docker", "ps", "--filter", "name=supabase_db_", "--format", "{{.Names}}"],  # noqa: S607
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    if len(names) != 1:
        raise SystemExit(f"se esperaba un contenedor supabase_db_*, hay {names}")
    return names[0]


async def _inject_chaos(run: Run, api: Api, *, after_s: float) -> str:
    await asyncio.sleep(after_s)
    started = time.monotonic()
    if run.mode is Mode.CHAOS_DB:
        container = _db_container()
        await asyncio.to_thread(
            subprocess.run,
            ["docker", "restart", "--time", "5", container],
            check=True,
            capture_output=True,
        )
        what = f"Postgres reiniciado ({container})"
    else:
        api.kill()
        await asyncio.sleep(3)  # la api está caída: Meta (k6) recibe conexión rechazada y reintenta
        api.start()
        await api.wait_healthy()
        what = "api muerta con SIGKILL y levantada de nuevo"
    return f"{what} a los {after_s:.0f} s; volvió en {time.monotonic() - started:.1f} s"


async def _run_k6(run: Run) -> int:
    command, env = _k6_command(run)
    with (run.out_dir / "k6.log").open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            *command, env={**os.environ, **env}, stdout=log, stderr=log
        )
        return await process.wait()


# --- Principal ------------------------------------------------------------------------------------
@contextlib.contextmanager
def _out_dir(path: str | None) -> Iterator[Path]:
    if path:
        directory = Path(path).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        yield directory
    else:
        with tempfile.TemporaryDirectory(prefix="scz-load-") as tmp:
            yield Path(tmp)


async def main_async(args: argparse.Namespace, out_dir: Path) -> int:
    _require_local(ADMIN_DSN)
    if shutil.which("docker") is None:
        raise SystemExit("hace falta Docker (k6 corre en su imagen oficial)")
    run_id = secrets.token_hex(4)
    run = Run(
        mode=Mode(args.mode),
        run_id=run_id,
        org_id=uuid4(),
        phone_number_id=f"PNID-LOAD-{run_id}",
        webhook_secret=secrets.token_urlsafe(32),
        backend_password=secrets.token_urlsafe(24),
        port=args.port,
        rate=args.rate,
        duration=args.duration,
        out_dir=out_dir,
    )
    print(f"corrida {run.run_id}: {run.mode.value}, {run.rate:g} rps durante {run.duration}")
    print(f"salida en {out_dir}")
    await _prepare_db(run)
    round_trip_ms = await _db_round_trip_ms(run)
    api = Api(run)
    chaos_note = ""
    try:
        api.start()
        await api.wait_healthy()
        await _warm_up(run, api)
        k6 = asyncio.create_task(_run_k6(run))
        if run.mode is not Mode.LOAD:
            chaos_note = await _inject_chaos(run, api, after_s=_duration_s(run.duration) / 3)
            print(f"caos: {chaos_note}")
        k6_exit = await k6
        if k6_exit != 0:
            print(f"k6 terminó con código {k6_exit} (ver {out_dir / 'k6.log'})")
    finally:
        api.stop()
        await _disable_backend_login()

    summary_path = out_dir / "summary.json"
    if not summary_path.exists():
        k6_log = out_dir / "k6.log"
        tail = (
            k6_log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:] if k6_log.exists() else []
        )
        raise SystemExit("k6 no dejó el resumen. Últimas líneas de k6.log:\n" + "\n".join(tail))
    result = K6Result.from_summary(json.loads(summary_path.read_text(encoding="utf-8")))
    counts = await _count(run)
    verdict = evaluate(run.mode, result, counts)
    report = render_markdown(
        run.mode, result, counts, verdict, target_rps=run.rate, db_round_trip_ms=round_trip_ms
    )
    if chaos_note:
        report += f"\nFalla inyectada: {chaos_note}.\n"
    print(report)
    (out_dir / f"report-{run.mode.value}.md").write_text(report, encoding="utf-8")
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        await asyncio.to_thread(_append, Path(step_summary), report + "\n")
    return 0 if verdict.passed else 1


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Carga y caos del webhook (#21)")
    parser.add_argument("mode", choices=[m.value for m in Mode])
    parser.add_argument("--rate", type=float, default=50, help="mensajes nuevos por segundo (default 50)")
    parser.add_argument("--duration", default="3m", help="duración de la carga, p. ej. 90s o 3m (default 3m)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", help="carpeta para el resumen, los logs y el reporte (default: temporal)")
    args = parser.parse_args()
    with _out_dir(args.out) as out_dir:
        return asyncio.run(main_async(args, out_dir))


if __name__ == "__main__":
    raise SystemExit(main())
