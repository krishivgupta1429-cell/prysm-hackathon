"""Built during the Meta Global AI Developer Hackathon (new work). Overall score normalisation."""
import pytest

from app.core.config import NICHE_WEIGHTS, DEFAULT_SCORED_WEIGHTS, SCORED_PARAMETERS, ADVISORY_PARAMETERS
from app.studio.score_math import normalise_scored_weights, overall_and_predicted


def params(score, advisory_score=10):
    out = [{"name": n, "score": score, "advisory": False} for n in SCORED_PARAMETERS]
    out += [{"name": n, "score": advisory_score, "advisory": True} for n in ADVISORY_PARAMETERS]
    return out


@pytest.mark.parametrize("niche", list(NICHE_WEIGHTS) + ["default"])
def test_six_parameters_at_70_give_an_overall_of_70_for_every_niche(niche):
    w = DEFAULT_SCORED_WEIGHTS if niche == "default" else NICHE_WEIGHTS[niche]
    overall, predicted = overall_and_predicted(params(70), w)
    assert overall == 70 and predicted == 75  # every scored param is below 75, so all are projected to 75


@pytest.mark.parametrize("niche", list(NICHE_WEIGHTS))
def test_normalised_weights_sum_to_one_and_cover_only_the_scored_parameters(niche):
    nw = normalise_scored_weights(NICHE_WEIGHTS[niche])
    assert set(nw) == set(SCORED_PARAMETERS) and sum(nw.values()) == pytest.approx(1.0)


def test_advisory_parameters_never_change_the_overall():
    w = NICHE_WEIGHTS["lifestyle"]
    assert overall_and_predicted(params(70, 0), w) == overall_and_predicted(params(70, 100), w)


def test_projection_uses_the_same_normalisation_and_never_drops_a_strong_score():
    w = NICHE_WEIGHTS["fitness"]
    p = params(90)
    assert overall_and_predicted(p, w) == (90, 90)          # strong scores stay as they are
    overall, predicted = overall_and_predicted(params(40), w)
    assert overall == 40 and predicted == 75                # weak ones are lifted to 75, on the same 0-100 scale


def test_unknown_weights_fall_back_to_equal_weights():
    assert overall_and_predicted(params(60), {})[0] == 60


def test_matches_the_production_result_weights():
    # the production share results use 19/17/16/16/16/16 (sum 100): 48,68,58,72,70,78 -> 65
    prod = {"Hook & Opening": 19, "Retention & Pacing": 17, "Shareability": 16, "Audio & Music": 16, "Trend Alignment": 16,
            "Visual Quality & Originality": 16}
    scores = dict(zip(prod, [48, 68, 58, 72, 70, 78]))
    p = [{"name": n, "score": s, "advisory": False} for n, s in scores.items()]
    assert overall_and_predicted(p, prod)[0] == 65
