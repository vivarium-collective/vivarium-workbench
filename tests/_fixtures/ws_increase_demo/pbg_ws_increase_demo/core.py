from process_bigraph import allocate_core
from process_bigraph.emitter import RAMEmitter
from pbg_ws_increase_demo.processes import GuardedIncreaseProcess, IncreaseProcess


def build_core():
    core = allocate_core()
    core.register_link('IncreaseProcess', IncreaseProcess)
    core.register_link('GuardedIncreaseProcess', GuardedIncreaseProcess)
    core.register_link('RAMEmitter', RAMEmitter)
    return core
