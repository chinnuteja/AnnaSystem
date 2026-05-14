from packages.core.phone_utils import graph_api_recipient, whatsapp_db_lookup_variants


def test_whatsapp_db_lookup_variants_meta_style():
    assert set(whatsapp_db_lookup_variants("919876543210")) == {
        "919876543210",
        "+919876543210",
    }


def test_whatsapp_db_lookup_variants_e164():
    assert set(whatsapp_db_lookup_variants("+919876543210")) == {
        "+919876543210",
        "919876543210",
    }


def test_graph_api_recipient():
    assert graph_api_recipient("+91 98765 43210") == "919876543210"
    assert graph_api_recipient("unknown") is None
    assert graph_api_recipient("") is None
