from dataclasses import dataclass

@dataclass
class IncomeProportionalityResult:
    ratio: float
    band: str  # "Proportionate" | "Elevated" | "High"
    requires_human_review: bool
    note: str
    cited_clause: str

def calculate_income_proportionality(
    requested_amount: float, annual_declared_income: float
) -> IncomeProportionalityResult:
    """
    Pure deterministic function. No LLM call. Bands:
      ratio <= 0.5            -> Proportionate, requires_human_review=False
      0.5 < ratio <= 1.5      -> Elevated, requires_human_review=True
      ratio > 1.5             -> High, requires_human_review=True
    Guard against annual_declared_income == 0 (raise ValueError, don't
    divide by zero or silently default).
    """
    if annual_declared_income == 0:
        raise ValueError("annual_declared_income cannot be zero.")
        
    ratio = requested_amount / annual_declared_income
    
    if ratio <= 0.5:
        band = "Proportionate"
        requires_human_review = False
    elif ratio <= 1.5:
        band = "Elevated"
        requires_human_review = True
    else:
        band = "High"
        requires_human_review = True
        
    return IncomeProportionalityResult(
        ratio=ratio,
        band=band,
        requires_human_review=requires_human_review,
        note=f"Income proportionality ratio is {ratio:.2f} ({band}).",
        cited_clause="Section I, Clause 3"
    )
