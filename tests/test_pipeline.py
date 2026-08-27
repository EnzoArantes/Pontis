"""The batch pipeline's validation layer, tested without a database or the CSV.

Each test builds the minimal synthetic Scorecard row that triggers one guard.
The guards under proof are the ones that keep a 22-school batch as honest as
the hand-checked five: identity by UNITID with a state tripwire, per-band
fields only (with the wrong-field signature failing hard), suppression as an
honest absence, and negative prices as real data.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.pipeline import (  # noqa: E402
    BANDS,
    ROSTER,
    SchoolResult,
    parse_money,
    validate_school,
)


def scorecard_row(state="MA", control="2", overall=20000,
                  bands=(4000, 7000, 13000, 20000, 60000), name="Test U"):
    suffix = "PUB" if control == "1" else "PRIV"
    row = {"INSTNM": name, "STABBR": state, "CONTROL": control,
           f"NPT4_{suffix}": str(overall)}
    for i, value in enumerate(bands, start=1):
        row[f"NPT4{i}_{suffix}"] = str(value)
    return row


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_wrong_state_fails_the_identity_tripwire():
    """UNITID resolving to another state means the wrong campus was bound --
    the Northeastern-Oakland / UMass-Global trap."""
    result = validate_school(9999, "MA", scorecard_row(state="CA"))
    assert not result.ok
    assert any("wrong institution" in f for f in result.failures)
    assert result.band_prices == {}


def test_missing_unitid_fails():
    result = validate_school(9999, "MA", None)
    assert not result.ok
    assert any("not found" in f for f in result.failures)


def test_for_profit_is_out_of_scope():
    result = validate_school(9999, "MA", scorecard_row(control="3"))
    assert not result.ok
    assert any("for-profit" in f for f in result.failures)


# ---------------------------------------------------------------------------
# The wrong-field signature and band handling
# ---------------------------------------------------------------------------


def test_five_identical_bands_fail_hard():
    """The signature of NPT4 (overall average) substituted for NPT41..45."""
    result = validate_school(
        9999, "MA", scorecard_row(bands=(20000,) * 5, overall=20000)
    )
    assert not result.ok
    assert any("identical" in f for f in result.failures)


def test_suppressed_band_is_an_absent_row_not_a_zero():
    result = validate_school(
        9999, "MA",
        scorecard_row(bands=(4000, "PrivacySuppressed", 13000, "NA", 60000)),
    )
    assert result.ok
    assert set(result.band_prices) == {"0-30k", "48-75k", "110k+"}
    assert 0 not in result.band_prices.values()
    assert sum("suppressed" in f for f in result.flags) == 2


def test_all_bands_missing_fails():
    result = validate_school(9999, "MA", scorecard_row(bands=("NA",) * 5))
    assert not result.ok
    assert any("no per-band net price" in f for f in result.failures)


def test_negative_price_is_ingested_and_flagged():
    """MIT's real shape: aid exceeds cost of attendance. Real data, kept."""
    result = validate_school(
        9999, "MA", scorecard_row(bands=(-2533, 93, 1480, 11555, 48479))
    )
    assert result.ok
    assert result.band_prices["0-30k"] == -2533
    assert any("negative" in f for f in result.flags)


def test_poorest_band_at_or_above_overall_average_is_flagged():
    """Contradicts the premise of need-based pricing; a human should look."""
    result = validate_school(
        9999, "MA",
        scorecard_row(bands=(25000, 7000, 13000, 20000, 60000), overall=20000),
    )
    assert result.ok
    assert any("not below the overall average" in f for f in result.flags)


def test_public_and_private_read_their_own_columns():
    pub = validate_school(9999, "CA", scorecard_row(state="CA", control="1"))
    assert pub.ok and pub.is_public is True
    priv = validate_school(9999, "CA", scorecard_row(state="CA", control="2"))
    assert priv.ok and priv.is_public is False


def test_parse_money_handles_scorecard_cell_shapes():
    assert parse_money("13787") == 13787
    assert parse_money("-2533.0") == -2533
    assert parse_money("NA") is None
    assert parse_money("PrivacySuppressed") is None
    assert parse_money("") is None


# ---------------------------------------------------------------------------
# Roster hygiene
# ---------------------------------------------------------------------------

# The confusable institutions the identity tests already reject in the seed;
# the batch roster must never pick them up either.
REJECTED_UNITIDS = {139861, 244437, 139621, 482158,
                    118888,   # Northeastern University Oakland (CA campus)
                    262086}   # "University of Massachusetts Global" (online, CA)


def test_roster_unitids_are_unique():
    unitids = [uid for uid, _ in ROSTER]
    assert len(set(unitids)) == len(unitids)


def test_roster_contains_no_rejected_institution():
    assert not REJECTED_UNITIDS & {uid for uid, _ in ROSTER}


def test_roster_still_contains_the_curated_five():
    """The batch path re-ingests the hand-checked schools; dropping one from
    the roster would silently stop re-verifying it."""
    assert {164924, 110635, 228778, 166629, 139940} <= {uid for uid, _ in ROSTER}


def test_report_lines_name_their_status():
    ok = SchoolResult(unitid=1, name="A", state="MA", is_public=True,
                      band_prices={"0-30k": 1000})
    assert ok.report_line().startswith("PASS")
    ok.flags.append("something")
    assert ok.report_line().startswith("FLAG")
    bad = SchoolResult(unitid=2, failures=["boom"])
    assert bad.report_line().startswith("FAIL")
