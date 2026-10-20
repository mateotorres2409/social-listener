import json
import shutil
from pathlib import Path

from social_listener.bootstrap import eval_classification as ev
from social_listener.domain.classification.catalog import DEFAULT_TAXONOMY, TYPE_VALUES

ROOT = Path(__file__).parents[2]


def test_conjunto_de_evaluacion_cubre_tipos_y_categorias() -> None:
    rows = ev.load_dataset(ev.DATASET, None)
    assert len(rows) >= 100
    assert {r["labels"]["tipo"] for r in rows} == set(TYPE_VALUES)
    categorias = {r["labels"]["categoria"] for r in rows} - {"indeterminado"}
    assert categorias == {c.code for c in DEFAULT_TAXONOMY.categories}


def test_comando_con_reglas_actualiza_el_reporte(tmp_path: Path) -> None:
    report = tmp_path / "clasificacion.md"
    shutil.copy(ROOT / "docs" / "clasificacion.md", report)

    result = ev.main(["--provider", "reglas", "--limit", "20", "--report", str(report)])
    ev.main(["--provider", "reglas", "--limit", "20", "--report", str(report)])

    assert result.total == 20
    table = report.read_text("utf-8").split(ev.START)[1].split(ev.END)[0]
    assert table.count("| reglas | léxicos |") == 1  # la segunda corrida reemplaza la fila


async def test_comando_con_modelo_falso(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from tests.fakes.services import ScriptedLLMClassifier
    from tests.unit.test_classify_mention import proposal

    class Registry:
        def build(self, route):  # type: ignore[no-untyped-def]
            return ScriptedLLMClassifier(responder=proposal())

    monkeypatch.setattr(ev, "default_registry", lambda secrets: Registry())
    route = ev.ModelRoute(provider="ollama", model="falso")
    rows = [json.loads(line) for line in ev.DATASET.read_text("utf-8").splitlines()[:5]]

    result = await ev.evaluate(route, rows)

    assert result.total == 5
    assert result.invalid == 0
    assert len(result.latencies) == 5
