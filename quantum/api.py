"""Bounded subprocess execution; shape validation is distinct from domain errors."""
import asyncio
import json
import os
import sys
import threading
import anyio
from typing import Literal
from fastapi import APIRouter, Request
from pydantic import ConfigDict, Field, model_validator
from contratos import Modelo, Erro
from .config import get_limits, validate_source
from .errors import CircuitError

router = APIRouter()
_active = 0
_lock = threading.Lock()

class Circuito(Modelo):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    qasm: str | None = Field(default=None, description="Entrada OpenQASM 2 legada; exclusiva com code.")
    code: str | None = Field(default=None, description="Código QASM2 ou Python/Qiskit restrito.")
    format: Literal["auto", "qasm", "python"] = "auto"
    optimization_level: int = Field(default=3, ge=0, le=3, strict=True)

    @model_validator(mode="after")
    def source_contract(self):
        if (self.qasm is None) == (self.code is None):
            raise ValueError("Informe exatamente um campo: qasm ou code.")
        if self.qasm is not None and self.format == "python":
            raise ValueError("Use code para Python; qasm contém somente OpenQASM 2.")
        return self

class ResultadoQiskit(Modelo):
    original: dict
    proposta: dict
    equivalente: bool | None
    status_verificacao: str
    motivo: str | None
    diferenca_operacoes: int
    diferenca_profundidade: int
    reduziu_operacoes: bool | None
    reduziu_profundidade: bool | None
    circuito_otimizado_qasm: str
    formato_detectado: Literal["qasm", "python"]
    formato_saida: Literal["qasm"] | None
    entrada_sha256: str
    tempo_processamento_segundos: float
    nivel_otimizacao: int
    passes_executados: list[str]
    otimizacoes_aplicadas: list[str]
    warnings: list[str]

class DetalheErroCircuito(Modelo):
    code: str
    message: str
    details: dict

class ErroCircuito(Erro):
    error: DetalheErroCircuito

@router.get("/qiskit/limits")
async def limites():
    return get_limits().public()

def worker_environment():
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LD_LIBRARY_PATH"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep or k.startswith("MAX_") or
           k in {"OPTIMIZATION_TIMEOUT_SECONDS", "QISKIT_TIMEOUT_SECONDS", "QISKIT_WORKER_MEMORY_MB"}}
    env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
               RAYON_NUM_THREADS="1", QISKIT_PARALLEL="FALSE", PYTHONDONTWRITEBYTECODE="1")
    return env

async def execute_worker(payload):
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable, "-m", "quantum.worker", env=worker_environment(),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    ))
    try:
        process = await asyncio.shield(spawning)
    except asyncio.CancelledError:
        # Even a timeout during process creation must not orphan a child.
        with anyio.CancelScope(shield=True):
            process = await spawning
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
        raise
    try:
        output, _ = await process.communicate(json.dumps(payload).encode())
        if process.returncode != 0:
            raise CircuitError("WORKER_RESOURCE_ERROR", "O processo Qiskit terminou sem resultado; verifique o orçamento de recursos.", status=503)
        try:
            response = json.loads(output)
        except (ValueError, UnicodeError) as exc:
            raise CircuitError("INVALID_WORKER_RESPONSE", "Resposta inválida do processo Qiskit.", status=502) from exc
        if "error" in response:
            failure = response["error"]
            raise CircuitError(failure["code"], failure["message"], failure["details"], response["status"])
        return response["result"]
    finally:
        with anyio.CancelScope(shield=True):
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()

async def execute_connected(payload, request):
    async def disconnected():
        # FastAPI has already consumed the complete body for schema validation.
        while (await request.receive())["type"] != "http.disconnect":
            pass
    worker = asyncio.create_task(execute_worker(payload))
    connection = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait((worker, connection), return_when=asyncio.FIRST_COMPLETED)
        if worker in done:
            return worker.result()
        raise CircuitError("CLIENT_DISCONNECTED", "O cliente encerrou a solicitação.", status=499)
    finally:
        with anyio.CancelScope(shield=True):
            for task in (worker, connection):
                if not task.done():
                    task.cancel()
            await asyncio.gather(worker, connection, return_exceptions=True)


@router.post("/qiskit/optimize", response_model=ResultadoQiskit,
             responses={status: {"model": ErroCircuito} for status in (400, 413, 415, 422, 429, 499, 500, 502, 503, 504)})
async def otimizar(entrada: Circuito, request: Request):
    """Analisa QASM2 ou Python restrito e transpila sem depender de LLM."""
    global _active
    limits = get_limits()
    source = entrada.qasm if entrada.qasm is not None else entrada.code
    validate_source(source, limits)
    with _lock:
        if _active >= limits.max_concurrent_optimizations:
            raise CircuitError("OPTIMIZER_BUSY", "O otimizador está ocupado. Tente novamente em instantes.", status=429)
        _active += 1
    try:
        payload = {"code": source, "format": "qasm" if entrada.qasm is not None else entrada.format,
                   "optimization_level": entrada.optimization_level}
        try:
            return await asyncio.wait_for(execute_connected(payload, request), limits.optimization_timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise CircuitError("OPTIMIZATION_TIMEOUT", "A análise excedeu o tempo configurado.",
                               {"limit_seconds": limits.optimization_timeout_seconds}, 504) from exc
    finally:
        with _lock:
            _active -= 1
