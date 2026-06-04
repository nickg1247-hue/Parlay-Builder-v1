import pandas as pd

from app.features.feature_selection import drop_redundant_features


def test_drop_redundant_removes_highly_correlated_column():
    df = pd.DataFrame(
        {
            "a": [1, 2, 3, 4, 5],
            "b": [1, 2, 3, 4, 5],
            "c": [10, 11, 9, 12, 8],
        }
    )
    kept, dropped, _ = drop_redundant_features(df, ["a", "b", "c"], threshold=0.9)
    assert "a" in kept
    assert "b" in dropped
    assert "c" in kept
