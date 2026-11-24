"""Every relationship, category, and tag in the catalog must say what supports it."""

from irs_research_server.services.irs_history import load_catalog

STRING_RELS = ("attaches_to", "related_instructions", "related_publications")
OBJECT_RELS = ("flows_into", "superseded_by", "split_from", "referenced_forms")
OBJECT_REVERSE = ("incoming_flows", "superseded_forms", "split_children", "referenced_by")


def _bases():
    return set(load_catalog()["provenance_bases"])


def test_every_edge_has_valid_provenance():
    bases = _bases()
    missing = []
    for prod, node in load_catalog()["documents"].items():
        for rel in STRING_RELS:
            for target in node["relationships"][rel]:
                prov = node["provenance"][rel].get(target)
                if not prov or prov["basis"] not in bases:
                    missing.append((prod, rel, target))
        for rel in OBJECT_RELS:
            for edge in node["relationships"][rel]:
                if edge.get("provenance", {}).get("basis") not in bases:
                    missing.append((prod, rel, edge))
        for rel in OBJECT_REVERSE:
            for edge in node["reverse_relationships"][rel]:
                if edge.get("provenance", {}).get("basis") not in bases:
                    missing.append((prod, rel, edge))
    assert not missing, f"{len(missing)} edges without provenance, e.g. {missing[:3]}"


def test_every_tag_and_category_has_provenance():
    bases = _bases()
    for prod, node in load_catalog()["documents"].items():
        assert node["provenance"]["category"]["basis"] in bases, prod
        for tag in node["tags"]:
            assert node["tag_provenance"].get(tag, {}).get("basis") in bases, (prod, tag)


def test_irs_text_edges_carry_a_quote():
    for prod, node in load_catalog()["documents"].items():
        for edge in node["relationships"]["referenced_forms"]:
            source = edge["provenance"]["source"]
            assert source["quote"] and source["pdf_url"].startswith("https://www.irs.gov/"), (prod, edge)


def test_schedule_parent_is_irs_naming():
    node = load_catalog()["documents"]["Form 1040 (Schedule 1)"]
    assert node["provenance"]["attaches_to"]["Form 1040"]["basis"] == "irs_naming"
