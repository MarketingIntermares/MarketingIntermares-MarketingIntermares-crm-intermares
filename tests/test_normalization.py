from src.normalization import normalize_email, normalize_phone


def test_normalize_email():
    assert normalize_email(" Test@Example.COM ") == "test@example.com"


def test_normalize_phone_brazil():
    assert normalize_phone("+55 (73) 99999-8888") == "73999998888"
