"""Prueba de punta a punta contra el entorno de Docker Compose (``deploy/``).

Requiere el entorno levantado (``docker compose -f deploy/docker-compose.yml up -d``) y
``SL_E2E=1``. Ingiere un fixture de menciones financieras con el conector ``fixture`` y
verifica los LogRecords que el Collector escribió en ``deploy/data/logs.jsonl``.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from social_listener.adapters.outbound.otel import schema as s

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("SL_E2E"), reason="define SL_E2E=1 con el entorno arriba"
    ),
]

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "deploy" / "fixtures" / "menciones.jsonl"
COLLECTOR_FILE = ROOT / "deploy" / "data" / "logs.jsonl"
API = os.environ.get("SL_E2E_API", "http://localhost:8000")
TOKEN = os.environ.get("SL_API_TOKEN", "token-local")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:1.7b")
TIMEOUT_S = float(os.environ.get("SL_E2E_TIMEOUT_S", "900"))


def fixture_rows(run: str) -> list[dict[str, Any]]:
    def row(key: str, text: str, username: str, **kw: Any) -> dict[str, Any]:
        return {
            "channel": "x",
            "external_id": f"{run}-{key}",
            "url": f"https://x.com/e2e/status/{run}-{key}",
            "text": text,
            "language": "es",
            "published_at": "2026-10-01T10:00:00+00:00",
            "author_external_id": kw.pop("author_id", f"{run}-autor-{key}"),
            "author_username": username,
            **kw,
        }

    return [
        row(
            "tarjeta",
            "Me cobraron doble en la tarjeta 4111 1111 1111 1111, exijo la devolución",
            "cliente_ana",
        ),
        row(
            "fraude",
            "Cuidado: hay un perfil falso del banco pidiendo claves por mensaje directo",
            "vecina_123",
        ),
        row(
            "influencer",
            "Pésimo el banco, la app lleva dos días caída y nadie responde",
            "influencer_top",
            author_followers=80_000,
            likes=900,
            shares=300,
        ),
        row("ambiguo", "¿Cómo solicito un aumento de cupo de la tarjeta?", "ambiguo_luis"),
        row(
            "oficial",
            "Hola, ya revisamos tu caso por mensaje privado",
            "banco_oficial",
            author_id=f"{run}-banco-oficial",
            parent_external_id=f"{run}-tarjeta",
            format="respuesta",
        ),
    ]


def read_logs(project_id: str) -> list[dict[str, Any]]:
    if not COLLECTOR_FILE.exists():
        return []
    found = []
    for line in COLLECTOR_FILE.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        request = json.loads(line)
        for rl in request.get("resourceLogs", []):
            resource = _attrs(rl.get("resource", {}).get("attributes", []))
            for sl in rl.get("scopeLogs", []):
                for record in sl.get("logRecords", []):
                    attributes = _attrs(record.get("attributes", []))
                    if attributes.get(s.PROJECT_ID) == project_id:
                        found.append({**record, "attrs": attributes, "resource": resource})
    return found


def _value(value: dict[str, Any]) -> Any:
    if "arrayValue" in value:
        return [_value(v) for v in value["arrayValue"].get("values", [])]
    for key in ("stringValue", "boolValue", "intValue", "doubleValue"):
        if key in value:
            return int(value[key]) if key == "intValue" else value[key]
    return None


def _attrs(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {item["key"]: _value(item["value"]) for item in items}


def test_pipeline_completo() -> None:
    run = uuid.uuid4().hex[:8]
    rows = fixture_rows(run)
    FIXTURE.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")

    headers = {"Authorization": f"Bearer {TOKEN}"}
    with httpx.Client(base_url=API, headers=headers, timeout=30) as api:
        project = api.post(
            "/v1/projects",
            json={
                "name": f"E2E {run}",
                "official_accounts": [{"channel": "x", "external_id": f"{run}-banco-oficial"}],
            },
        ).json()
        chain = api.put(
            f"/v1/projects/{project['id']}/model-chain",
            json={
                "chain": [
                    {
                        "provider": "ollama",
                        "model": MODEL,
                        "base_url": "http://ollama:11434",
                        "timeout_s": 180,
                    }
                ]
            },
        )
        assert chain.status_code == 200, chain.text
        query = api.post(
            f"/v1/projects/{project['id']}/listening-queries",
            json={"name": "E2E", "channels": ["fixture"], "terms": ["banco"]},
        )
        assert query.status_code == 201, query.text

    deadline = time.monotonic() + TIMEOUT_S
    logs: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        logs = [
            log for log in read_logs(project["id"]) if log.get("eventName") == s.MENTION_ENRICHED
        ]
        if len({log["attrs"][s.MENTION_ID] for log in logs}) >= len(rows):
            break
        time.sleep(5)
    by_key = {log["attrs"][s.MENTION_URL].rsplit("-", 1)[1]: log for log in logs}
    assert set(by_key) == {"tarjeta", "fraude", "influencer", "ambiguo", "oficial"}, by_key.keys()

    for log in by_key.values():
        attrs = log["attrs"]
        # Seis dimensiones y esquema completo
        assert not [a for a in s.REQUIRED_MENTION_ATTRIBUTES if a not in attrs]
        # Sin atributos prohibidos ni datos personales en claro
        assert not s.FORBIDDEN_ATTRIBUTES & set(attrs)
        dump = json.dumps(log, ensure_ascii=False)
        for secret in ("4111 1111 1111 1111", "cliente_ana", "ambiguo_luis", "influencer_top"):
            assert secret not in dump
        assert log["resource"]["service.name"] == "social-listener"
        assert log.get("traceId")

    tarjeta = by_key["tarjeta"]
    assert "[TARJETA]" in tarjeta["body"]["stringValue"]
    assert tarjeta["attrs"][s.SENSITIVE_DATA_TYPES] == ["tarjeta"]
    assert tarjeta["attrs"][s.IDENTITY_STATUS] == "identificado"
    assert tarjeta["attrs"][s.IDENTITY_CUSTOMER_PSEUDONYM].startswith("k-local:")

    assert by_key["ambiguo"]["attrs"][s.IDENTITY_STATUS] == "ambiguo"
    assert s.IDENTITY_CUSTOMER_PSEUDONYM not in by_key["ambiguo"]["attrs"]
    assert by_key["oficial"]["attrs"][s.IDENTITY_STATUS] == "no_identificado"
    assert by_key["oficial"]["attrs"][s.THREAD_ID] == tarjeta["attrs"][s.THREAD_ID]

    assert by_key["influencer"]["severityText"] in {"WARN", "ERROR"}
    assert by_key["fraude"]["severityText"] in {"WARN", "ERROR"}

    by_model = [log for log in by_key.values() if s.CLASSIFIER_PROVIDER in log["attrs"]]
    assert by_model, "ninguna mención fue clasificada por el modelo de Ollama"
    for log in by_model:
        assert log["attrs"][s.CLASSIFIER_PROVIDER] == "ollama"
        assert log["attrs"][s.CLASSIFIER_MODEL] == MODEL
