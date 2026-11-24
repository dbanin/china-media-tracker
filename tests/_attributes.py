"""The paper's six attributes and their spans, as a reply or session line under prompt 2026-12-01 must carry
them (classify_llm.OUTPUT_SCHEMA, labels.validate), chosen so the category the paper's rule derives from them
is the category given (classify_llm.attribute_disagreement finds nothing)."""


def attribute_fields(category: str = "B", issuer_type: str = "none") -> dict:
    """The ten attribute fields for a reply of this category and issuer type."""
    if category == "A":
        author = "soe" if issuer_type == "state_owned_enterprise" else "state"
        return {"author": author, "commissioner": "none", "state_affiliation": "yes", "route": "agency_syndication",
                "disclosure": "names_state_source", "verification": "no_official_claim", "author_evidence": None,
                "commissioner_evidence": None, "disclosure_evidence": None, "central_claim": None}
    issuer = issuer_type == "private_release"
    return {"author": "issuer_nonstate" if issuer else "newsroom", "commissioner": "issuer_nonstate" if issuer else "none",
            "state_affiliation": "no", "route": "paid_release_wire" if issuer else "ordinary_reporting",
            "disclosure": "names_wire_or_partner_only" if issuer else "none",
            "verification": "merely_attributed" if category == "B" else "no_official_claim",
            "author_evidence": None, "commissioner_evidence": None, "disclosure_evidence": None,
            "central_claim": None}
