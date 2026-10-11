"""Serialize a process's contract + run the static contract audit, for the
registry/composite catalog payloads (spec section 8, server side).

Named contract_* to avoid the workbench's existing L0-L5 study "audit".
Degrades to {'status': 'unavailable'} when the workspace env lacks the
bigraph-schema contract machinery; never raises out of a catalog build.
"""
try:
    from bigraph_schema.contract_audit import audit_contract, completeness
    from bigraph_schema.assembly import contract_of
    from bigraph_schema.contract import ProcessContract
    CONTRACT_AUDIT_AVAILABLE = True
    try:
        # Same scoring predicate completeness() uses for a port; lets us report
        # typed/untyped per port instead of only the rolled-up grade.
        from bigraph_schema.contract_audit import _port_constrained
    except Exception:  # noqa: BLE001 - absent in older bigraph-schema
        _port_constrained = None
except Exception:  # noqa: BLE001 - older bigraph-schema in a workspace env
    CONTRACT_AUDIT_AVAILABLE = False
    ProcessContract = ()  # isinstance(x, ()) is always False — harmless sentinel
    _port_constrained = None

_INCOMPLETE_BELOW = 0.5


def _completeness_detail(face, conditions):
    """Break the opaque completeness grade into the two parts it rewards, so the
    UI can say *what* is missing rather than only showing a number.

    Mirrors ``bigraph_schema.contract_audit.completeness``:
    ``grade = 0.75 * (typed_ports / total_ports) + (0.25 if any predicate else 0)``.
    The two returned weights always sum to the grade, so the panel can show the
    exact arithmetic and a concrete "to improve" list.
    """
    face = face or {}
    ports = []  # (direction, name, schema)
    for direction in ('inputs', 'outputs'):
        for name, schema in (face.get(direction) or {}).items():
            ports.append((direction, name, schema))
    if _port_constrained is not None:
        untyped = [f'{d}.{n}' for (d, n, s) in ports if not _port_constrained(s)]
    else:
        untyped = []
    n_ports = len(ports)
    n_typed = n_ports - len(untyped)
    port_score = (n_typed / n_ports) if n_ports else 0.0
    has_pred = bool(conditions)
    missing = []
    if n_ports and untyped:
        missing.append(
            f'type the port{"s" if len(untyped) != 1 else ""} ' + ', '.join(untyped)
            + ' (add _type / _units / bounds)')
    elif not n_ports:
        missing.append('declare input/output ports')
    if not has_pred:
        missing.append('declare predicate conditions (bounds or invariants via narrow amendments)')
    return {
        'ports_typed': n_typed,
        'ports_total': n_ports,
        'untyped_ports': untyped,
        'port_weight': round(0.75 * port_score, 3),   # out of 0.75
        'has_predicates': has_pred,
        'n_predicates': len(conditions),
        'predicate_weight': 0.25 if has_pred else 0.0,  # out of 0.25
        'missing': missing,
    }


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
        # A class may store ``contract`` as a plain dict (hand-authored,
        # serialized form) rather than a ProcessContract. ``audit_contract``
        # needs the dataclass (reads ``contract.face``), so coerce via
        # ``contract_of`` — which builds a proper ProcessContract (typed face
        # from the process's ports) — whenever we don't already have one. Else
        # a dict-contract process audits as {'status':'error'}.
        if contract is None or not isinstance(contract, ProcessContract):
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
        detail = _completeness_detail(face, conditions)
    except Exception as error:  # noqa: BLE001 - one process must not sink the catalog
        return {'status': 'error', 'message': str(error)}

    if any(f['severity'] == 'error' for f in findings):
        status = 'fail'
    elif grade < _INCOMPLETE_BELOW:
        status = 'incomplete'
    else:
        status = 'pass'

    return {
        'status': status,
        'grade': round(grade, 3),
        'completeness': detail,
        'ports': {'inputs': _serialize_ports(face, 'inputs'), 'outputs': _serialize_ports(face, 'outputs')},
        'conditions': conditions,
        'findings': findings,
    }
