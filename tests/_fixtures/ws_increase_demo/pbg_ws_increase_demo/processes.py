from process_bigraph import Process


class IncreaseProcess(Process):
    """Trivial linear-growth process for the explorer test fixture."""
    config_schema = {'rate': {'_type': 'float', '_default': 1.0}}

    def inputs(self):
        return {'level': 'float'}

    def outputs(self):
        return {'level': 'float'}

    def update(self, state, interval=1.0):
        rate = (self.config or {}).get('rate', 1.0)
        return {'level': state.get('level', 0.0) * rate}


class GuardedIncreaseProcess(IncreaseProcess):
    """IncreaseProcess that raises once ``level`` exceeds ``max_level``, like a
    solver's divergence guard: a process failing mid-run (#1292)."""
    config_schema = {**IncreaseProcess.config_schema,
                     'max_level': {'_type': 'float', '_default': 1e300}}

    def update(self, state, interval=1.0):
        level = state.get('level', 0.0)
        if level > self.config['max_level']:
            raise RuntimeError(f"level={level:g} exceeded max_level={self.config['max_level']:g}")
        return super().update(state, interval)
