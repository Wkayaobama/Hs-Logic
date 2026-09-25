from app.entities import Marker, get_registry


def test_seed_loads_all_entities():
    r = get_registry()
    assert r.ids == ["WISEKEY", "WISESAT", "SEALSQ", "SEALCOIN", "QUANTUM_AI", "ICALPS", "MIRAEX", "WECAN"]
    assert r.get("ICALPS").parent == "SEALSQ"
    assert r.get("MIRAEX").brand_id == 2676052


def test_scope_modes():
    r = get_registry()
    assert r.scope("SEALSQ", "deals") == ("search", [{"filters": [{"propertyName": "pipeline", "operator": "IN", "values": ["12096408", "13772279"]}]}])
    assert r.scope("ICALPS", "tickets")[0] == "search"
    assert r.scope("WISEKEY", "contacts") == ("complement", None)
    assert r.scope("WECAN", "deals") == ("no_marker", None)
    assert r.scope("MIRAEX", "companies") == ("no_marker", None)
    assert r.scope("SEALSQ", "notes") == ("unscoped", None)
    assert r.scope(None, "deals") == ("all", None)


def test_marker_semantics_including_multi_checkbox():
    assert Marker(property="x", operator="HAS_PROPERTY").matches({"x": "v"})
    assert not Marker(property="x", operator="HAS_PROPERTY").matches({"x": ""})
    assert Marker(property="b", operator="EQ", value="2676052").matches({"b": "0;2676052"})
    assert not Marker(property="b", operator="EQ", value="2676052").matches({"b": "0"})
    assert Marker(property="p", operator="IN", values=["a", "b"]).matches({"p": "b"})
    assert Marker(property="p", operator="CONTAINS_TOKEN", value="Sat").matches({"p": "Wise_Sat"})


def test_complement_and_classify():
    r = get_registry()
    assert r.classify("contacts", {"miraex_intent": "Careers"}) == ["MIRAEX"]
    assert r.classify("contacts", {"email": "a@b.c"}) == ["WISEKEY"]
    assert r.classify("deals", {"pipeline": "766126206"}) == ["ICALPS"]
    assert r.classify("deals", {"pipeline": "40296576"}) == ["WISEKEY"]
    assert r.matches("WISEKEY", "contacts", {"sealsq": "true"}) is False
    assert r.matches("WISEKEY", "contacts", {"wisekey_department": "Finance"}) is True
    assert "miraex_intent" in r.marker_properties("contacts")
