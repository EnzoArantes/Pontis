"""The REST layer preserves the engine's contract.

DB-backed (the loader reads Postgres); skips cleanly without one. What is
locked here is not engine arithmetic -- the engine suite owns that -- but the
contract the wire format must keep: two axes never blended, unknown and
not-ingested as first-class states, the ceiling shown term by term, reasons
attached to every exclusion, and definitional input rejections surfacing as
422s with their explanations intact.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

psycopg = pytest.importorskip("psycopg")
fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from ingest.db import connect  # noqa: E402


@pytest.fixture(scope="module")
def client():
    try:
        conn = connect()
        conn.close()
    except psycopg.OperationalError as exc:
        pytest.skip(f"no reachable Pontis database: {exc}")
    from api.main import app

    with TestClient(app) as test_client:
        yield test_client


GA_STUDENT = {
    "state": "GA",
    "family_income": 28000,
    "family_size": 4,
    "gpa_value": "3.6",
    "gpa_scale": "unweighted",
}


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["database"] == "reachable"


def test_match_returns_two_axes_and_no_blended_score(client):
    response = client.post("/api/match", json=GA_STUDENT)
    assert response.status_code == 200
    payload = response.json()

    assert set(payload) == {"ceiling", "on_your_list", "not_on_your_list"}
    everything = payload["on_your_list"] + payload["not_on_your_list"]
    assert everything
    for entry in everything:
        assert set(entry) == {"school_name", "admissions", "affordability"}
        assert "score" not in str(sorted(entry["admissions"])).lower()
        assert entry["affordability"]["reason"]


def test_ceiling_shows_its_work(client):
    payload = client.post("/api/match", json=GA_STUDENT).json()
    ceiling = payload["ceiling"]
    assert ceiling["family_term"] + ceiling["work_term"] + ceiling["loan_term"] \
        == ceiling["ceiling"] == 7125
    assert ceiling["wage_is_federal_fallback"] is True
    assert "Family contribution" in ceiling["explain"]


def test_georgia_student_sees_both_truths_at_gsu(client):
    """TARGET on one axis, unaffordable on the other, side by side."""
    payload = client.post("/api/match", json=GA_STUDENT).json()
    gsu = next(
        entry for entry in payload["not_on_your_list"]
        if entry["school_name"] == "Georgia State University"
    )
    assert gsu["admissions"]["category"] == "target"
    assert gsu["admissions"]["basis"] == "band_distribution"
    assert gsu["affordability"]["verdict"] == "unaffordable"
    assert gsu["affordability"]["gap"] == 6662


def test_unknown_cost_is_a_rendered_state_not_a_blank(client):
    """Out-of-state publics for a GA student: unknown, with the explanation."""
    payload = client.post("/api/match", json=GA_STUDENT).json()
    unknowns = [
        entry for entry in payload["not_on_your_list"]
        if entry["affordability"]["verdict"] == "unknown"
    ]
    assert unknowns
    for entry in unknowns:
        assert entry["affordability"]["net_price"] is None
        assert "unknown" in entry["affordability"]["reason"].lower()


def test_batch_school_without_stats_is_our_gap_not_theirs(client):
    """A pipeline-ingested school with no curated admissions row must say the
    gap is Pontis's."""
    payload = client.post("/api/match", json=GA_STUDENT).json()
    everything = payload["on_your_list"] + payload["not_on_your_list"]
    harvard = next(e for e in everything if e["school_name"] == "Harvard University")
    assert harvard["admissions"]["basis"] == "no_data_ingested"
    assert "gap in Pontis's data" in harvard["admissions"]["reason"]
    # The cost axis still works from the batch data alone -- and its verdict
    # is a real finding: Harvard's published $0-30k band ($8,697) exceeds
    # this student's $7,125 ceiling.
    assert harvard["affordability"]["verdict"] == "unaffordable"
    assert harvard["affordability"]["net_price"] == 8697


def test_negative_net_price_flows_through(client):
    """MIT for a low-income student: affordable, with the negative price shown."""
    payload = client.post("/api/match", json=GA_STUDENT).json()
    mit = next(
        entry for entry in payload["on_your_list"]
        if entry["school_name"] == "Massachusetts Institute of Technology"
    )
    assert mit["affordability"]["verdict"] == "affordable"
    assert mit["affordability"]["net_price"] == -2533


def test_definitional_rejections_surface_as_422(client):
    bad_rank = dict(GA_STUDENT, class_rank_percentile=-5)
    response = client.post("/api/match", json=bad_rank)
    assert response.status_code == 422
    assert "class_rank_percentile" in response.json()["detail"]

    bad_gpa = dict(GA_STUDENT, gpa_value="4.3")
    response = client.post("/api/match", json=bad_gpa)
    assert response.status_code == 422
    assert "unweighted" in response.json()["detail"]


def test_schools_endpoint_reports_data_coverage(client):
    payload = client.get("/api/schools").json()
    by_name = {s["name"]: s for s in payload["schools"]}
    assert len(by_name) >= 22
    assert by_name["Georgia State University"]["has_band_distribution"] is True
    assert by_name["Harvard University"]["has_admissions_data"] is False
    assert by_name["Boston College"]["meets_full_need"] is True
