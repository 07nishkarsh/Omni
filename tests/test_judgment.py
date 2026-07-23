import pytest
from app.orchestrator.judgment import calculate_income_proportionality, IncomeProportionalityResult

def test_calculate_income_proportionality_zero_income():
    with pytest.raises(ValueError, match="annual_declared_income cannot be zero."):
        calculate_income_proportionality(1000.0, 0.0)

@pytest.mark.parametrize(
    "requested_amount, annual_income, expected_ratio, expected_band, expected_requires_human",
    [
        (500.0, 1000.0, 0.5, "Proportionate", False),
        (500.01, 1000.0, 0.50001, "Elevated", True),
        (1000.0, 1000.0, 1.0, "Elevated", True),
        (1500.0, 1000.0, 1.5, "Elevated", True),
        (1500.01, 1000.0, 1.50001, "High", True),
        (2000.0, 1000.0, 2.0, "High", True),
        (10.0, 1000.0, 0.01, "Proportionate", False),
    ]
)
def test_calculate_income_proportionality_bands(
    requested_amount, annual_income, expected_ratio, expected_band, expected_requires_human
):
    result = calculate_income_proportionality(requested_amount, annual_income)
    
    assert pytest.approx(result.ratio, 0.0001) == expected_ratio
    assert result.band == expected_band
    assert result.requires_human_review == expected_requires_human
    assert result.cited_clause == "Section I, Clause 3"
