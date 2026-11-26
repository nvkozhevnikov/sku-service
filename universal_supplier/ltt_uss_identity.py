"""Source identity decisions; names, prices and URL suffixes are never keys."""


def source_pair_relationship(left: dict, right: dict) -> str:
    """Compare two captures within one namespace without invoking matching.

    Distinct stable IDs take precedence over similar names and URL shapes.
    Without stable IDs a shared final redirect target is explicit alias evidence;
    otherwise retain both candidates until source evidence resolves identity.
    """
    if left.get('supplier') != right.get('supplier') or not left.get('supplier'):
        return 'SEPARATE_SUPPLIER_NAMESPACES'
    first, second = left.get('source_id'), right.get('source_id')
    if first and second:
        return 'SAME_SOURCE_ENTITY' if first == second else 'DISTINCT_SOURCE_ENTITIES'
    final = left.get('final_url')
    if final and final == right.get('final_url') and (
            left.get('requested_url') != final or right.get('requested_url') != final):
        return 'EXPLICIT_REDIRECT_ALIAS'
    # Shared canonical values require a separately validated source assertion;
    # requested URLs or common model/title text do not establish this evidence.
    canonical = left.get('source_canonical_url')
    if (canonical and canonical == right.get('source_canonical_url')
            and left.get('canonical_source_verified') is True
            and right.get('canonical_source_verified') is True):
        return 'EXPLICIT_CANONICAL_ALIAS'
    return 'IDENTITY_UNRESOLVED_KEEP_BOTH'
