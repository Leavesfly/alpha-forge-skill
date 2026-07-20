"""多因子选股包。"""

from __future__ import annotations

from .analysis import (
    compute_ic,
    factor_correlation,
    factor_decay,
    ic_summary,
    neutralize,
)
from .library import FACTORS, PRICE_FACTORS, compute_factor
from .model import FactorResult, run_factor_model
from .preprocess import composite_score, standardize

__all__ = [
    "FACTORS",
    "PRICE_FACTORS",
    "compute_factor",
    "FactorResult",
    "run_factor_model",
    "composite_score",
    "standardize",
    "compute_ic",
    "ic_summary",
    "factor_decay",
    "factor_correlation",
    "neutralize",
]
