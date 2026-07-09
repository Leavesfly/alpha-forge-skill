"""多因子选股包。"""

from __future__ import annotations

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
]
