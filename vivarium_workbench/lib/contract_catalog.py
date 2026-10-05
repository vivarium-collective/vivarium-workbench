"""Serialize a process's contract + run the static contract audit, for the
registry/composite catalog payloads (spec section 8, server side).

Named contract_* to avoid the workbench's existing L0-L5 study "audit".
Degrades to {'status': 'unavailable'} when the workspace env lacks the
bigraph-schema contract machinery; never raises out of a catalog build.
"""
try:
    from bigraph_schema.contract_audit import audit_contract, completeness
    from bigraph_schema.assembly import contract_of
    CONTRACT_AUDIT_AVAILABLE = True
except Exception:  # noqa: BLE001 - older bigraph-schema in a workspace env
    CONTRACT_AUDIT_AVAILABLE = False

_INCOMPLETE_BELOW = 0.5


def _serialize_ports(face, direction):
    out = {}
    for port, schema in (face.get(direction) or {}).items():
        if isinstance(schema, dict):
            out[port] = {k: schema.get(k) for k in ('_type', '_min', '_max', '_units') if k in schema}
        else:
            out[port] = {'_type': schema}
    return out


def contract_audit_payload(core, address):
    """The contract + audit summary for one registered process address.

    status: pass | fail | incomplete | not-declared | unavailable | error.
    """
    if not CONTRACT_AUDIT_AVAILABLE:
        return {'status': 'unavailable'}
    try:
        # Get the process class from link_registry to access its contract with amendments
        process_cls = core.link_registry.get(address)
        if process_cls is None and ':' in address:
            # local:-prefixed addresses: fall back to the bare registry key
            process_cls = core.link_registry.get(address.split(':')[-1])
        if process_cls is None:
            return {'status': 'not-declared'}
        contract = getattr(process_cls, 'contract', None)
        if contract is None:
            # Try contract_of as fallback
            contract = contract_of(core, address)
    except Exception as error:  # noqa: BLE001
        return {'status': 'error', 'message': str(error)}
    if contract is None:
        return {'status': 'not-declared'}
    try:
        report = audit_contract(core, contract)
        grade = completeness(contract)
        face = getattr(contract, 'face', None) or {}
        # Extract conditions from amendments (where narrow_condition stores them)
        conditions = []
        for amendment in getattr(contract, 'amendments', []):
            if 'condition' in amendment.detail:
                cond = amendment.detail['condition']
                conditions.append({'kind': cond.get('kind'), 'name': cond.get('name'), 'expr': cond.get('expr')})
        findings = [{'severity': f.severity, 'code': f.code, 'where': f.where, 'message': f.message}
                    for f in report.findings]
    except Exception as error:  # noqa: BLE001 - one process must not sink the catalog
        return {'status': 'error', 'message': str(error)}

    if any(f['severity'] == 'error' for f in findings):
        status = 'fail'
    elif not conditions and grade < _INCOMPLETE_BELOW:
        status = 'incomplete'
    elif grade < _INCOMPLETE_BELOW:
        status = 'incomplete'
    else:
        status = 'pass'

    return {
        'status': status,
        'grade': round(grade, 3),
        'ports': {'inputs': _serialize_ports(face, 'inputs'), 'outputs': _serialize_ports(face, 'outputs')},
        'conditions': conditions,
        'findings': findings,
    }
