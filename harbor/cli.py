"""Command line entry points: `harbor serve|demo|ingest|seed|chat|eval`."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import typer

from harbor.config import get_settings
from harbor.container import KNOWLEDGE_DIR, SEED_DIR, build_container, build_offline_container
from harbor.logging import configure_logging

app = typer.Typer(help="Harbor: RAG + multi-agent support assistant", no_args_is_help=True)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000, reload: bool = False) -> None:
    """Run the API against Postgres and the configured providers."""
    import uvicorn

    uvicorn.run("harbor.api.app:app", host=host, port=port, reload=reload)


@app.command()
def demo(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the API fully offline (scripted LLM, in-memory index). No keys, no database."""
    import uvicorn

    from harbor.api.app import create_app

    uvicorn.run(create_app(get_settings(), offline=True), host=host, port=port)


@app.command()
def ingest(path: Path = typer.Argument(KNOWLEDGE_DIR), force: bool = False) -> None:
    """Chunk, embed and index a folder of markdown/text files (idempotent by content hash)."""

    async def _run() -> None:
        c = await build_container(get_settings())
        try:
            report = await c.ingestion.ingest_path(path, force=force)
            typer.echo(json.dumps(report.as_dict(), indent=2))
        finally:
            await c.close()

    configure_logging()
    asyncio.run(_run())


@app.command()
def seed() -> None:
    """Load the sample customers and orders into Postgres."""
    from harbor.db.repositories import OrderRepository

    async def _run() -> None:
        c = await build_container(get_settings())
        try:
            data = json.loads((SEED_DIR / "orders.json").read_text())
            orders = []
            for o in data["orders"]:
                row = dict(o)
                for key in ("placed_at", "shipped_at", "delivered_at"):
                    if row.get(key):
                        row[key] = datetime.fromisoformat(row[key])
                orders.append(row)
            async with c.sessions() as session:
                created = await OrderRepository(session).seed(data["customers"], orders)
                await session.commit()
            typer.echo(f"seeded {created} new orders ({len(data['customers'])} customers)")
        finally:
            await c.close()

    configure_logging()
    asyncio.run(_run())


@app.command()
def chat(offline: bool = False, email: str | None = None) -> None:
    """Interactive terminal chat. --offline needs no database or keys."""

    async def _run() -> None:
        settings = get_settings()
        c = build_offline_container(settings) if offline else await build_container(settings)
        conversation_id: str | None = None
        typer.echo("Harbor ready. Type a message, or /quit.")
        try:
            while True:
                text = (await asyncio.to_thread(input, "> ")).strip()
                if text in ("/quit", "/exit", ""):
                    break
                result = await c.runtime.run_turn(
                    message=text, conversation_id=conversation_id, customer_email=email
                )
                conversation_id = result.conversation_id
                typer.echo(
                    f"\n[{result.intent} | {result.latency_ms} ms | {'/'.join(result.trace)}]\n{result.answer}"
                )
                for cit in result.citations:
                    typer.echo(f"  {cit['id']}: {cit['source']} ({cit['section']})")
                typer.echo("")
        finally:
            await c.close()

    configure_logging(level="WARNING")
    asyncio.run(_run())


@app.command("eval")
def eval_cmd(
    offline: bool = False,
    ragas: bool = typer.Option(False, help="also compute Ragas metrics (needs real LLM + embeddings)"),
    redteam: bool = True,
    out: Path = Path("evals/reports"),
    limit: int | None = None,
) -> None:
    """Run the golden set, LLM-as-judge, retrieval metrics and the injection red-team suite."""
    from harbor.evals.run import run_eval

    async def _run() -> None:
        settings = get_settings()
        c = build_offline_container(settings) if offline else await build_container(settings)
        try:
            summary = await run_eval(
                c, use_ragas=ragas and not offline, use_redteam=redteam, out_dir=out, limit=limit
            )
            typer.echo(json.dumps(summary, indent=2))
        finally:
            await c.close()

    configure_logging(level="WARNING")
    asyncio.run(_run())


@app.command()
def rescore(report_dir: Path, offline: bool = False) -> None:
    """Recompute the Ragas metrics of an existing eval report from its saved answers and contexts."""
    from harbor.evals.run import rescore_ragas

    async def _run() -> None:
        settings = get_settings()
        c = build_offline_container(settings) if offline else await build_container(settings)
        try:
            typer.echo(json.dumps(await rescore_ragas(c, report_dir), indent=2))
        finally:
            await c.close()

    configure_logging(level="WARNING")
    asyncio.run(_run())


if __name__ == "__main__":
    app()
