"""Evalúa la clasificación con un proveedor y modelo contra un conjunto etiquetado.

Uso::

    uv run python -m social_listener.bootstrap.eval_classification --provider reglas
    uv run python -m social_listener.bootstrap.eval_classification \\
        --provider ollama --model qwen3:4b --base-url http://localhost:11434

Reporta la precisión por dimensión (tipo, sentimiento, categoría), la latencia y la
tasa de salidas inválidas, y actualiza la tabla de resultados de ``docs/clasificacion.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from social_listener.adapters.outbound.privacy.rule_detector import RuleBasedDetector
from social_listener.adapters.outbound.secrets.provider import EnvSecretProvider
from social_listener.application.dto.classification import ModelRoute
from social_listener.application.errors import InvalidModelOutputError, ModelUnavailableError
from social_listener.application.use_cases.classification.prompt import (
    ClassificationPromptBuilder,
    MentionContext,
)
from social_listener.bootstrap.models import default_registry
from social_listener.domain.classification.catalog import (
    DEFAULT_TAXONOMY,
    SENTIMENT_VALUES,
    TYPE_VALUES,
)
from social_listener.domain.classification.fallback import classify_with_fallback
from social_listener.domain.classification.model import INDETERMINADO, Dimension
from social_listener.domain.privacy.policies import mask

ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "eval" / "menciones_financieras.jsonl"
REPORT = ROOT / "docs" / "clasificacion.md"
START, END = "<!-- resultados:inicio -->", "<!-- resultados:fin -->"
DIMENSIONS = ("tipo", "sentimiento", "categoria")
RULES = "reglas"


@dataclass
class EvalResult:
    provider: str
    model: str
    total: int = 0
    correct: dict[str, int] = field(default_factory=lambda: dict.fromkeys(DIMENSIONS, 0))
    invalid: int = 0
    unavailable: int = 0
    latencies: list[float] = field(default_factory=list)

    def accuracy(self, dimension: str) -> float:
        return self.correct[dimension] / self.total if self.total else 0.0

    @property
    def invalid_rate(self) -> float:
        return self.invalid / self.total if self.total else 0.0

    def latency(self, quantile: float) -> float:
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        return ordered[min(len(ordered) - 1, int(quantile * len(ordered)))]

    def row(self, when: str) -> str:
        return (
            f"| {self.provider} | {self.model} | {self.total} | "
            + " | ".join(f"{self.accuracy(d):.0%}" for d in DIMENSIONS)
            + f" | {self.invalid_rate:.0%} | {self.unavailable} | "
            f"{statistics.median(self.latencies) if self.latencies else 0:.2f} s | "
            f"{self.latency(0.95):.2f} s | {when} |"
        )


def load_dataset(path: Path, limit: int | None) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    return rows[:limit] if limit else rows


def _context(example: dict[str, Any]) -> MentionContext:
    masked = mask(example["text"], RuleBasedDetector().detect(example["text"]))
    return MentionContext(masked.value, example["channel"], "publicacion", "es", False)


async def evaluate(route: ModelRoute | None, examples: list[dict[str, Any]]) -> EvalResult:
    builder = ClassificationPromptBuilder()
    classifier = None if route is None else default_registry(EnvSecretProvider()).build(route)
    result = EvalResult(route.provider if route else RULES, route.model if route else "léxicos")
    categories = {c.code for c in DEFAULT_TAXONOMY.categories} | {INDETERMINADO}
    for example in examples:
        context = _context(example)
        labels = example["labels"]
        result.total += 1
        started = time.perf_counter()
        if classifier is None:
            fallback = classify_with_fallback(context.masked_text, DEFAULT_TAXONOMY)
            predicted = {
                "tipo": fallback.mention_type.value,
                "sentimiento": fallback.sentiment.label.value,
                "categoria": fallback.category,
            }
        else:
            request = builder.build(context, DEFAULT_TAXONOMY, [])
            try:
                proposal = await classifier.classify(request)
            except InvalidModelOutputError:
                result.invalid += 1
                continue
            except ModelUnavailableError:
                result.unavailable += 1
                continue
            finally:
                result.latencies.append(time.perf_counter() - started)
            allowed = {
                "tipo": TYPE_VALUES,
                "sentimiento": SENTIMENT_VALUES,
                "categoria": categories,
            }
            predicted = {}
            for name, dim in (
                ("tipo", Dimension.TIPO),
                ("sentimiento", Dimension.SENTIMIENTO),
                ("categoria", Dimension.CATEGORIA),
            ):
                value = proposal.values[dim].value
                predicted[name] = value if value in allowed[name] else INDETERMINADO
        if classifier is None:
            result.latencies.append(time.perf_counter() - started)
        for dimension in DIMENSIONS:
            result.correct[dimension] += int(predicted[dimension] == labels[dimension])
    return result


def update_report(result: EvalResult, report: Path = REPORT) -> None:
    text = report.read_text("utf-8")
    head, rest = text.split(START, 1)
    table, tail = rest.split(END, 1)
    lines = [line for line in table.strip().splitlines() if line.strip()]
    header, rows = lines[:2], lines[2:]
    key = f"| {result.provider} | {result.model} |"
    rows = [r for r in rows if not r.startswith(key)]
    rows.append(result.row(datetime.now(UTC).strftime("%Y-%m-%d")))
    rows.sort()
    report.write_text(head + START + "\n" + "\n".join(header + rows) + "\n" + END + tail, "utf-8")


def parse_route(args: argparse.Namespace) -> ModelRoute | None:
    if args.provider == RULES:
        return None
    options = dict(item.split("=", 1) for item in args.option)
    return ModelRoute(
        provider=args.provider,
        model=args.model,
        timeout_s=args.timeout,
        base_url=args.base_url,
        credential_ref=args.credential_ref,
        capabilities=frozenset(args.capability),
        options=options,
    )


def main(argv: list[str] | None = None) -> EvalResult:
    parser = argparse.ArgumentParser(prog="eval_classification")
    parser.add_argument("--provider", required=True, help=f"'{RULES}' o un proveedor del registro")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url")
    parser.add_argument("--credential-ref", help="referencia a SL_SECRET_<REF> en el entorno")
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--option", action="append", default=[], help="clave=valor")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args(argv)

    route = parse_route(args)
    if route is not None:
        default_registry(EnvSecretProvider()).validate([route])
    result = asyncio.run(evaluate(route, load_dataset(args.dataset, args.limit)))
    print(
        f"{result.provider}/{result.model}: "
        + ", ".join(f"{d}={result.accuracy(d):.0%}" for d in DIMENSIONS)
        + f", inválidas={result.invalid_rate:.0%}, no disponibles={result.unavailable}"
        + f", latencia p50={statistics.median(result.latencies) if result.latencies else 0:.2f}s"
    )
    if not args.no_report:
        update_report(result, args.report)
    return result


if __name__ == "__main__":
    main()
