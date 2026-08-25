import pandas as pd

from src.segmentation import segment


def test_segment_excludes_members_and_converted():
    base1 = pd.DataFrame([
        {"nome": "A", "telefone": "73999990001", "email": "a@x.com", "pms": ""},
        {"nome": "B", "telefone": "73999990002", "email": "b@x.com", "pms": "RES123"},
    ])
    base2 = pd.DataFrame([
        {"nome": "A duplicado", "telefone": "73999990001", "email": "a2@x.com", "pms": ""},
        {"nome": "C", "telefone": "73999990003", "email": "c@x.com", "pms": ""},
    ])
    members = pd.DataFrame([
        {"nome": "C", "telefone": "73999990003", "email": "c@x.com"}
    ])

    result = segment(base1, base2, members)

    assert len(result.audience) == 1
    assert result.excluded_converted == 1
    assert result.excluded_members == 1
    assert result.duplicates_removed == 1
